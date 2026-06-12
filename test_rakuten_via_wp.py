"""
WordPressサーバー経由でRakuten APIをテストする。
既存の /premier-blog/v1/rakuten-product エンドポイントを使用。
"""
import os, base64, requests, json
from dotenv import load_dotenv
load_dotenv()

ssl = os.environ.get('SSL_VERIFY','true').lower() != 'false'
token = base64.b64encode(f"{os.environ['WP_USERNAME']}:{os.environ['WP_APP_PASSWORD']}".encode()).decode()
auth_headers = {'Authorization': f'Basic {token}'}

app_id = 'becf4164-d48c-47bd-b61f-8fbdb1091f8c'
access_key = os.environ.get('RAKUTEN_APP_ID', '')
aff_id = os.environ.get('RAKUTEN_WS_AFFILIATE_ID', '')

print(f'accessKey: {access_key[:10]}...')
print(f'affiliateId: {aff_id}')
print()

# キーワードをいくつか試す
for kw in ['サッカー', 'soccer', 'プレミアリーグ', 'マンチェスターユナイテッド']:
    r = requests.get(
        'https://premier-blog.com/wp-json/premier-blog/v1/rakuten-product',
        params={
            'applicationId': app_id,
            'accessKey': access_key,
            'affiliateId': aff_id,
            'keyword': kw,
        },
        headers=auth_headers,
        verify=ssl, timeout=25
    )
    data = r.json()
    if 'Items' in data:
        item = data['Items'][0]['Item']
        print(f'[{kw}] 成功!')
        print(f'  商品: {item["itemName"][:50]}')
        print(f'  価格: ¥{item["itemPrice"]:,}')
        imgs = item.get('mediumImageUrls', [])
        print(f'  画像: {imgs[0]["imageUrl"] if imgs else "none"}')
        break
    else:
        print(f'[{kw}] → {json.dumps(data, ensure_ascii=False)[:100]}')
