import zipfile, io, os, base64, requests
from dotenv import load_dotenv
load_dotenv()

ssl   = os.environ.get('SSL_VERIFY','true').lower() != 'false'
token = base64.b64encode(f"{os.environ['WP_USERNAME']}:{os.environ['WP_APP_PASSWORD']}".encode()).decode()
AUTH  = {'Authorization': f'Basic {token}', 'Content-Type': 'application/json'}
BASE  = 'https://premier-blog.com'

# 新しいエンドポイント: Refererを複数形式で試しながら商品取得
NEW_ENDPOINT = r"""
// 楽天商品検索 中継エンドポイント v2（Referer自動調整）
add_action('rest_api_init', function() {
    register_rest_route('premier-blog/v1', '/rakuten-product', [
        'methods'             => 'GET',
        'callback'            => function(WP_REST_Request $req) {
            $app_id     = $req->get_param('applicationId');
            $access_key = $req->get_param('accessKey');
            $aff_id     = $req->get_param('affiliateId') ?: '';
            $keyword    = $req->get_param('keyword');
            if (!($app_id && $access_key && $keyword)) {
                return new WP_Error('missing_params', 'applicationId, accessKey, keyword required', ['status' => 400]);
            }
            $api_url = 'https://openapi.rakuten.co.jp/ichibams/api/IchibaItem/Search/20260401?' . http_build_query([
                'applicationId' => $app_id,
                'accessKey'     => $access_key,
                'affiliateId'   => $aff_id,
                'keyword'       => $keyword,
                'hits'          => 1,
                'imageFlag'     => 1,
                'sort'          => '-reviewCount',
            ]);

            // Referer候補を順番に試す
            $referers = [
                'https://premier-blog.com/',
                'https://www.premier-blog.com/',
                home_url('/'),
            ];
            foreach ($referers as $ref) {
                $resp = wp_remote_get($api_url, [
                    'timeout' => 10,
                    'headers' => [
                        'Referer'    => $ref,
                        'User-Agent' => 'Mozilla/5.0 (compatible; premier-blog/1.0)',
                    ],
                ]);
                if (is_wp_error($resp)) continue;
                $code = wp_remote_retrieve_response_code($resp);
                $body = json_decode(wp_remote_retrieve_body($resp), true);
                if ($code === 200 && isset($body['Items'])) {
                    return rest_ensure_response($body);
                }
                if ($code !== 403) break;  // 403以外のエラーはループ終了
            }
            // 全Referer失敗 - 最後のレスポンスを返す
            return rest_ensure_response($body ?? ['error' => 'all_referers_failed']);
        },
        'permission_callback' => function() {
            return current_user_can('manage_options');
        },
    ]);
});
"""

with zipfile.ZipFile('premier-blog-theme.zip') as z:
    functions_php = z.read('functions.php').decode('utf-8')
    all_files = {n: z.read(n) for n in z.namelist()}

# 古いrakuten-productエンドポイントを新しいものに置き換え
if 'rakuten-product' in functions_php:
    # 古いブロックを削除して新しいものを追加
    start = functions_php.find('// 楽天商品検索 中継エンドポイント')
    if start == -1:
        start = functions_php.find('rakuten-product')
        start = functions_php.rfind('//', 0, start)
    # エンドポイントブロックの終わりを探す（});の後）
    end = functions_php.find('\n});', start) + 4
    new_functions = functions_php[:start].rstrip() + '\n' + NEW_ENDPOINT + '\n' + functions_php[end:].lstrip()
else:
    new_functions = functions_php.rstrip() + '\n' + NEW_ENDPOINT + '\n'

r = requests.post(
    f'{BASE}/wp-json/premier-blog/v1/update-file',
    json={'file': 'functions.php', 'content': new_functions},
    headers=AUTH, verify=ssl, timeout=30,
)
print('functions.php更新:', 'OK' if r.ok else f'NG({r.status_code}) {r.text[:200]}')

if r.ok:
    all_files['functions.php'] = new_functions.encode('utf-8')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zout:
        for n, d in all_files.items():
            zout.writestr(n, d)
    with open('premier-blog-theme.zip', 'wb') as f:
        f.write(buf.getvalue())
    print('zip更新: OK')
