import zipfile, io, os, base64, requests
from dotenv import load_dotenv
load_dotenv()

ssl   = os.environ.get('SSL_VERIFY','true').lower() != 'false'
token = base64.b64encode(f"{os.environ['WP_USERNAME']}:{os.environ['WP_APP_PASSWORD']}".encode()).decode()
AUTH  = {'Authorization': f'Basic {token}', 'Content-Type': 'application/json'}
BASE  = 'https://premier-blog.com'

# cURLを直接使うエンドポイント（wp_remote_getのReferer問題を回避）
NEW_ENDPOINT = r"""
// 楽天商品検索 中継エンドポイント（cURL直接使用でReferer確実送信）
add_action('rest_api_init', function() {
    register_rest_route('premier-blog/v1', '/rakuten-product', [
        'methods'             => 'GET',
        'callback'            => function(WP_REST_Request $req) {
            $app_id     = $req->get_param('applicationId');
            $access_key = $req->get_param('accessKey');
            $aff_id     = $req->get_param('affiliateId') ?: '';
            $keyword    = $req->get_param('keyword');
            if (!($app_id && $access_key && $keyword)) {
                return new WP_Error('missing_params', 'params required', ['status' => 400]);
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

            if (!function_exists('curl_init')) {
                return new WP_Error('no_curl', 'cURL not available', ['status' => 500]);
            }
            $ch = curl_init($api_url);
            curl_setopt_array($ch, [
                CURLOPT_RETURNTRANSFER => true,
                CURLOPT_TIMEOUT        => 10,
                CURLOPT_REFERER        => home_url('/'),
                CURLOPT_USERAGENT      => 'Mozilla/5.0 (compatible; premier-blog/1.0)',
                CURLOPT_SSL_VERIFYPEER => true,
                CURLOPT_FOLLOWLOCATION => true,
            ]);
            $body = curl_exec($ch);
            $http_code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
            $curl_error = curl_error($ch);
            curl_close($ch);

            if ($curl_error) {
                return new WP_Error('curl_error', $curl_error, ['status' => 502]);
            }
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

# 既存のrakuten-productブロックを置き換え
marker = '// 楽天商品検索 中継エンドポイント'
start = functions_php.find(marker)
if start != -1:
    # ブロック終端を探す（'});' の後の改行）
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
