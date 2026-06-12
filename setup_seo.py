"""
OGP・構造化データ・DAZNアフィリエイト枠を一括セットアップ。
"""
import os, base64, zipfile, io
from dotenv import load_dotenv
load_dotenv()

from theme_updater import push_with_diff

ssl   = os.environ.get('SSL_VERIFY', 'true').lower() != 'false'
import base64 as _b64
token = _b64.b64encode(
    f"{os.environ['WP_USERNAME']}:{os.environ['WP_APP_PASSWORD']}".encode()
).decode()
AUTH  = {'Authorization': f'Basic {token}'}
BASE  = 'https://premier-blog.com'


def push_file(filename, content):
    return push_with_diff(filename, content, AUTH, BASE, ssl)


OGP_BLOCK = """\
<!-- OGP / Twitter Card -->
<?php
$_og_title = is_singular() ? get_the_title() : get_bloginfo('name');
$_og_desc  = is_singular() ? wp_strip_all_tags(get_the_excerpt()) : get_bloginfo('description');
$_og_url   = is_singular() ? get_permalink() : home_url('/');
$_og_img   = (is_singular() && has_post_thumbnail()) ? get_the_post_thumbnail_url(null, 'hero-main') : '';
?>
<meta property="og:type" content="<?php echo is_singular() ? 'article' : 'website'; ?>" />
<meta property="og:title" content="<?php echo esc_attr($_og_title); ?>" />
<meta property="og:description" content="<?php echo esc_attr($_og_desc); ?>" />
<meta property="og:url" content="<?php echo esc_url($_og_url); ?>" />
<?php if ($_og_img): ?><meta property="og:image" content="<?php echo esc_url($_og_img); ?>" /><?php endif; ?>
<meta property="og:site_name" content="<?php echo esc_attr(get_bloginfo('name')); ?>" />
<meta property="og:locale" content="ja_JP" />
<meta name="twitter:card" content="summary_large_image" />
<meta name="twitter:title" content="<?php echo esc_attr($_og_title); ?>" />
<meta name="twitter:description" content="<?php echo esc_attr($_og_desc); ?>" />
<?php if ($_og_img): ?><meta name="twitter:image" content="<?php echo esc_url($_og_img); ?>" /><?php endif; ?>
"""

JSON_LD = """\

<!-- NewsArticle 構造化データ -->
<?php if (is_singular('post')): ?>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "NewsArticle",
  "headline": "<?php echo esc_js(get_the_title()); ?>",
  "datePublished": "<?php echo get_the_date('c'); ?>",
  "dateModified": "<?php echo get_the_modified_date('c'); ?>",
  "author": {"@type":"Person","name":"<?php echo esc_js(get_the_author_meta('display_name')); ?>"},
  "publisher": {"@type":"Organization","name":"<?php echo esc_js(get_bloginfo('name')); ?>","url":"<?php echo esc_url(home_url('/')); ?>"},
  "url": "<?php echo esc_url(get_permalink()); ?>",
  "description": "<?php echo esc_js(wp_strip_all_tags(get_the_excerpt())); ?>"
  <?php if (has_post_thumbnail()): ?>,"image":"<?php echo esc_url(get_the_post_thumbnail_url(null,'hero-main')); ?>"<?php endif; ?>
}
</script>
<?php endif; ?>
"""


def main():
    with zipfile.ZipFile('premier-blog-theme.zip') as z:
        header_php = z.read('header.php').decode('utf-8')
        single_php = z.read('single.php').decode('utf-8')
        all_files  = {n: z.read(n) for n in z.namelist()}

    changed = False

    # 1. header.php に OGP 追加
    if 'og:title' not in header_php:
        header_php = header_php.replace(
            '<meta name="viewport"', OGP_BLOCK + '<meta name="viewport"'
        )
        print('[1] header.php: OGP追加')
        changed = True
    else:
        print('[1] header.php: OGP既存スキップ')

    # 2. single.php に NewsArticle JSON-LD 追加
    if 'NewsArticle' not in single_php:
        single_php = single_php.replace(
            '<?php get_footer(); ?>', JSON_LD + '\n<?php get_footer(); ?>'
        )
        print('[2] single.php: NewsArticle追加')
        changed = True
    else:
        print('[2] single.php: NewsArticle既存スキップ')

    # 3. WordPress へ送信
    print('\n--- WordPress へ反映 ---')
    push_file('header.php', header_php)
    push_file('single.php', single_php)

    # 4. zip 更新
    all_files['header.php'] = header_php.encode('utf-8')
    all_files['single.php'] = single_php.encode('utf-8')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zout:
        for n, d in all_files.items():
            zout.writestr(n, d)
    with open('premier-blog-theme.zip', 'wb') as f:
        f.write(buf.getvalue())
    print('\n[完了] zip・サイト両方を更新済み')


if __name__ == '__main__':
    main()
