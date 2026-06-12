import zipfile, io, os, base64, requests
from dotenv import load_dotenv
load_dotenv()

ssl   = os.environ.get('SSL_VERIFY','true').lower() != 'false'
token = base64.b64encode(f"{os.environ['WP_USERNAME']}:{os.environ['WP_APP_PASSWORD']}".encode()).decode()
AUTH  = {'Authorization': f'Basic {token}', 'Content-Type': 'application/json'}
BASE  = 'https://premier-blog.com'

RAKUTEN_PROXY_ENDPOINT = r"""
// 楽天商品検索 中継エンドポイント（サーバー側でRakuten APIを呼ぶ）
add_action('rest_api_init', function() {
    register_rest_route('premier-blog/v1', '/rakuten-product', [
        'methods'             => 'GET',
        'callback'            => function(WP_REST_Request $req) {
            $app_id     = $req->get_param('applicationId');
            $access_key = $req->get_param('accessKey');
            $aff_id     = $req->get_param('affiliateId');
            $keyword    = $req->get_param('keyword');
            if (!($app_id && $access_key && $keyword)) {
                return new WP_Error('missing_params', 'applicationId, accessKey, keyword required', ['status' => 400]);
            }
            $url = add_query_arg([
                'applicationId' => $app_id,
                'accessKey'     => $access_key,
                'affiliateId'   => $aff_id ?: '',
                'keyword'       => $keyword,
                'hits'          => 1,
                'imageFlag'     => 1,
                'sort'          => '-reviewCount',
            ], 'https://openapi.rakuten.co.jp/ichibams/api/IchibaItem/Search/20260401');

            $resp = wp_remote_get($url, [
                'timeout' => 10,
                'headers' => [
                    'Referer'    => home_url('/'),
                    'User-Agent' => 'premier-blog/1.0',
                ],
            ]);
            if (is_wp_error($resp)) {
                return new WP_Error('rakuten_error', $resp->get_error_message(), ['status' => 502]);
            }
            $body = wp_remote_retrieve_body($resp);
            $data = json_decode($body, true);
            return rest_ensure_response($data);
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

if 'rakuten-product' in functions_php:
    print('既存スキップ')
else:
    new_functions = functions_php.rstrip() + '\n' + RAKUTEN_PROXY_ENDPOINT + '\n'

    r = requests.post(
        f'{BASE}/wp-json/premier-blog/v1/update-file',
        json={'file': 'functions.php', 'content': new_functions},
        headers=AUTH, verify=ssl, timeout=30,
    )
    print('functions.php更新:', 'OK' if r.ok else f'NG({r.status_code}) {r.text[:100]}')

    all_files['functions.php'] = new_functions.encode('utf-8')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zout:
        for n, d in all_files.items():
            zout.writestr(n, d)
    with open('premier-blog-theme.zip', 'wb') as f:
        f.write(buf.getvalue())
    print('zip更新: OK')
