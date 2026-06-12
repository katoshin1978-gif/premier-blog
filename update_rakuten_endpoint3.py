import zipfile, io, os, base64, requests
from dotenv import load_dotenv
load_dotenv()

ssl   = os.environ.get('SSL_VERIFY','true').lower() != 'false'
token = base64.b64encode(f"{os.environ['WP_USERNAME']}:{os.environ['WP_APP_PASSWORD']}".encode()).decode()
AUTH  = {'Authorization': f'Basic {token}', 'Content-Type': 'application/json'}
BASE  = 'https://premier-blog.com'

NEW_ENDPOINT = r"""
// 楽天商品検索 中継エンドポイント（cURL + ハードコードReferer）
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

            $ch = curl_init($api_url);
            curl_setopt_array($ch, [
                CURLOPT_RETURNTRANSFER => true,
                CURLOPT_TIMEOUT        => 10,
                CURLOPT_REFERER        => 'https://premier-blog.com/',
                CURLOPT_USERAGENT      => 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                CURLOPT_SSL_VERIFYPEER => true,
                CURLOPT_FOLLOWLOCATION => true,
                CURLOPT_HTTPHEADER     => [
                    'Origin: https://premier-blog.com',
                    'Referer: https://premier-blog.com/',
                ],
            ]);
            $body     = curl_exec($ch);
            $curl_err = curl_error($ch);
            curl_close($ch);

            if ($curl_err) {
                return ['curl_error' => $curl_err];
            }
            return rest_ensure_response(json_decode($body, true));
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

marker = '// 楽天商品検索 中継エンドポイント'
start = functions_php.find(marker)
end = functions_php.find('\n});', start) + 4
new_functions = functions_php[:start].rstrip() + '\n' + NEW_ENDPOINT + '\n' + functions_php[end:].lstrip()

r = requests.post(
    f'{BASE}/wp-json/premier-blog/v1/update-file',
    json={'file': 'functions.php', 'content': new_functions},
    headers=AUTH, verify=ssl, timeout=30,
)
print('更新:', 'OK' if r.ok else f'NG({r.status_code}) {r.text[:200]}')

if r.ok:
    all_files['functions.php'] = new_functions.encode('utf-8')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zout:
        for n, d in all_files.items():
            zout.writestr(n, d)
    with open('premier-blog-theme.zip', 'wb') as f:
        f.write(buf.getvalue())
    print('zip: OK')
