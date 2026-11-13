<?php
/* Ez2 one-time login (f1fae 11-11). ?ez2_otl=<token>: the site stores only sha256(token) in a
 * transient for 600 s; the key is deleted before use (one use); only an administrator is logged in. */
add_action('init', function () {
    if (!isset($_GET['ez2_otl']) || !is_string($_GET['ez2_otl'])) { return; }
    $t = $_GET['ez2_otl'];
    if (!preg_match('/^[A-Za-z0-9]{43}$/', $t)) { return; }
    $key = 'ez2_otl_' . hash('sha256', $t);
    $uid = get_transient($key);
    if ($uid === false) { return; }
    if (!delete_transient($key)) { return; }   // one use is kept by the database: only the request that deleted the key goes on (c477a N1)
    $u = get_user_by('id', (int) $uid);
    if (!$u || !user_can($u, 'manage_options')) { return; }
    nocache_headers();
    wp_set_current_user($u->ID);
    wp_set_auth_cookie($u->ID, false, is_ssl());
    do_action('wp_login', $u->user_login, $u);
    wp_safe_redirect(admin_url());
    exit;
}, 1);
