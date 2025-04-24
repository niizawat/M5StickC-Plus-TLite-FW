import json
import boto3
import numpy as np
from PIL import Image
import io
from datetime import datetime, UTC
import os
from typing import List, Dict, Any, Union

# カラーマップのルックアップテーブルを事前生成（256段階）
COLORMAP = [(0, int(255 * (i/85)), 255) if i < 85 else  # 低温域（青→シアン）
           (int(255 * ((i-85)/85)), 255, int(255 * (1 - (i-85)/85))) if i < 170 else  # 中温域（シアン→黄）
           (255, int(255 * (1 - (i-170)/85)), 0)  # 高温域（黄→赤）
           for i in range(256)]

def validate_temperature_data(temperature_data: List[float]) -> None:
    """
    温度データのバリデーションを行う
    
    Args:
        temperature_data (List[float]): 検証する温度データ
        
    Raises:
        ValueError: データが無効な場合
    """
    if not temperature_data:
        raise ValueError("温度データが空です")
    if len(temperature_data) != 768:  # 16x24x2 (2フレーム分)
        raise ValueError(f"温度データの要素数が無効です: {len(temperature_data)} (768要素である必要があります)")
    if not all(isinstance(x, (int, float)) for x in temperature_data):
        raise ValueError("温度データに数値以外の要素が含まれています")

def create_thermal_image(temperature_data: List[float], width: int = 32, height: int = 24) -> io.BytesIO:
    """
    温度データからサーマルイメージを生成する
    
    Args:
        temperature_data (List[float]): 温度データのリスト（768要素）
        width (int): 画像の幅（デフォルト: 32）
        height (int): 画像の高さ（デフォルト: 24）
    
    Returns:
        BytesIO: PNGイメージのバイトストリーム
    """
    # 1次元配列を2次元配列に変換（2フレームを1つの画像として結合）
    temp_array = np.array(temperature_data).reshape(height, width)
    
    # 温度データの正規化（0-255の範囲に）
    temp_min = np.min(temp_array)
    temp_max = np.max(temp_array)
    normalized = ((temp_array - temp_min) / (temp_max - temp_min) * 255).astype(np.uint8)
    
    # 画像の作成（64x48にスケーリング）
    img = Image.new('RGB', (width*2, height*2))
    pixels = img.load()
    
    # バイリニア補間でスケーリング
    for y in range(height*2):
        for x in range(width*2):
            src_x = x / 2
            src_y = y / 2
            x0 = int(np.floor(src_x))
            x1 = min(x0 + 1, width-1)
            y0 = int(np.floor(src_y))
            y1 = min(y0 + 1, height-1)
            
            fx = src_x - x0
            fy = src_y - y0
            
            c00 = normalized[y0, x0]
            c10 = normalized[y0, x1]
            c01 = normalized[y1, x0]
            c11 = normalized[y1, x1]
            
            value = int((1-fx)*(1-fy)*c00 + fx*(1-fy)*c10 + (1-fx)*fy*c01 + fx*fy*c11)
            pixels[x, y] = COLORMAP[value]
    
    # PNGとしてバイトストリームに保存
    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format='PNG', optimize=True)
    img_byte_arr.seek(0)
    
    return img_byte_arr

def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda関数のメインハンドラー
    
    Args:
        event (Dict[str, Any]): Lambda関数のイベントデータ
        context (Any): Lambda関数のコンテキスト
    
    Returns:
        Dict[str, Any]: 処理結果
    """
    try:
        print(event)
        print(context)
        
        # イベントボディの解析
        if 'body' not in event:
            raise ValueError("リクエストボディが見つかりません")
            
        body = json.loads(event['body']) if isinstance(event['body'], str) else event['body']
        
        # payloadキーの存在確認
        if 'payload' not in body:
            raise ValueError("payloadが見つかりません")
        
        # payloadからデータを取得
        payload = body['payload']
        
        # クラウド接続機能からのデータ形式に対応
        macaddr = payload.get('macaddr')  # MACアドレス
        temperature_data = payload.get('frame')  # 温度データ配列
            
        if not temperature_data:
            raise ValueError("temperature_dataが指定されていません")
            
        # 温度データのバリデーション
        validate_temperature_data(temperature_data)
        
        timestamp = datetime.now(UTC)
        
        # デバイスから送信された統計値を使用
        stats = {
            'center_temp': float(payload.get('center', 0.0)),
            'avg_temp': float(payload.get('average', 0.0)),
            'max_temp': float(payload.get('highest', 0.0)),
            'min_temp': float(payload.get('lowest', 0.0)),
            'std_temp': 0.0,  # デバイスから送信されないため0.0を設定
            'median_temp': 0.0  # デバイスから送信されないため0.0を設定
        }
        
        # CloudWatchメトリクスに記録
        cloudwatch = boto3.client('cloudwatch')
        metric_data = [
            {
                'MetricName': name.replace('_', '').title(),
                'Value': value,
                'Unit': 'None',
                'Timestamp': timestamp,
                'Dimensions': [{'Name': 'MacAddress', 'Value': macaddr}]
            }
            for name, value in stats.items()
        ]
        
        cloudwatch.put_metric_data(
            Namespace='ThermalMetrics',
            MetricData=metric_data
        )
        
        # サーマルイメージの生成
        thermal_image = create_thermal_image(temperature_data)
        
        # S3にアップロード
        s3 = boto3.client('s3')
        timestamp_str = timestamp.strftime('%Y%m%d_%H%M%S')
        key = f'thermal_images/{macaddr}/{timestamp_str}.png'
        
        metadata = {
            'macaddr': macaddr,
            'timestamp': timestamp_str,
            'datetime': payload.get('datetime', ''),  # 元のタイムスタンプを保存
            'interval': str(payload.get('interval', '')),  # アップロード間隔を保存
            'pwd': payload.get('pwd', ''),  # 認証トークンを保存
            **{k: str(v) for k, v in stats.items()}
        }
        
        s3.upload_fileobj(
            thermal_image,
            os.environ['BUCKET_NAME'],
            key,
            ExtraArgs={
                'ContentType': 'image/png',
                'Metadata': metadata
            }
        )
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'データを正常に処理しました',
                'image_key': key,
                'metrics': stats,
                'timestamp': timestamp_str
            })
        }
        
    except ValueError as e:
        print(f"バリデーションエラー: {str(e)}")
        return {
            'statusCode': 400,
            'body': json.dumps({
                'error': 'バリデーションエラー',
                'message': str(e)
            })
        }
    except Exception as e:
        print(f"予期せぬエラーが発生しました: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': '内部サーバーエラー',
                'message': '予期せぬエラーが発生しました。システム管理者に連絡してください。'
            })
        }
