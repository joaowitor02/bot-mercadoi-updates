<?php
/**
 * Plugin Name: Bot Mercadoi API
 * Description: REST API para o Bot Mercadoi publicar imóveis sem abrir o navegador.
 * Version:     1.0.0
 * Author:      Bot Mercadoi
 */

defined('ABSPATH') || exit;

class BotMercadoiAPI {

    const NS             = 'bot-mercadoi/v1';
    const OPTION_KEY     = 'bot_mercadoi_api_key';
    const POST_TYPE      = 'property';   // post type do tema Houzez

    public function __construct() {
        add_action('rest_api_init',  [$this, 'register_routes']);
        add_action('admin_menu',     [$this, 'admin_menu']);
        add_action('admin_init',     [$this, 'admin_init']);
    }

    // -------------------------------------------------------------------------
    // Admin
    // -------------------------------------------------------------------------

    public function admin_menu(): void {
        add_submenu_page(
            'tools.php',
            'Bot Mercadoi API',
            'Bot Mercadoi API',
            'manage_options',
            'bot-mercadoi-api',
            [$this, 'admin_page']
        );
    }

    public function admin_init(): void {
        register_setting('bot_mercadoi_group', self::OPTION_KEY, [
            'sanitize_callback' => 'sanitize_text_field',
        ]);
    }

    public function admin_page(): void {
        $api_key = get_option(self::OPTION_KEY, '');

        // Gera nova chave se solicitado
        if (
            isset($_POST['gerar_chave'])
            && check_admin_referer('bot_mercadoi_gerar')
        ) {
            $api_key = bin2hex(random_bytes(32));
            update_option(self::OPTION_KEY, $api_key);
            echo '<div class="notice notice-success"><p>Nova chave gerada com sucesso.</p></div>';
        }

        // Apaga chave
        if (
            isset($_POST['apagar_chave'])
            && check_admin_referer('bot_mercadoi_gerar')
        ) {
            delete_option(self::OPTION_KEY);
            $api_key = '';
            echo '<div class="notice notice-warning"><p>Chave removida. A API está desativada.</p></div>';
        }

        $site_url = get_site_url();
        ?>
        <div class="wrap">
            <h1>Bot Mercadoi — Configuração da API</h1>

            <table class="form-table">
                <tr>
                    <th>URL base da API</th>
                    <td><code><?php echo esc_html($site_url); ?>/wp-json/<?php echo self::NS; ?>/</code></td>
                </tr>
                <tr>
                    <th>Chave de API (Bearer)</th>
                    <td>
                        <?php if ($api_key): ?>
                            <code style="background:#f0f0f0;padding:6px 10px;display:inline-block;word-break:break-all;">
                                <?php echo esc_html($api_key); ?>
                            </code>
                            <p class="description">Cole esta chave no <code>config.json</code> do bot como <code>wordpress_api_key</code>.</p>
                        <?php else: ?>
                            <em>Nenhuma chave configurada. A API está desativada.</em>
                        <?php endif; ?>
                    </td>
                </tr>
            </table>

            <form method="post">
                <?php wp_nonce_field('bot_mercadoi_gerar'); ?>
                <button name="gerar_chave" class="button button-primary">
                    <?php echo $api_key ? 'Gerar nova chave (invalida a anterior)' : 'Gerar chave de API'; ?>
                </button>
                <?php if ($api_key): ?>
                    &nbsp;
                    <button name="apagar_chave" class="button button-secondary"
                            onclick="return confirm('Isso desativa a API. Confirmar?')">
                        Remover chave
                    </button>
                <?php endif; ?>
            </form>

            <hr>
            <h2>Endpoints disponíveis</h2>
            <ul>
                <li><code>POST  /wp-json/<?php echo self::NS; ?>/properties</code> — cria imóvel</li>
                <li><code>POST  /wp-json/<?php echo self::NS; ?>/properties/{id}/media</code> — sobe imagens</li>
                <li><code>GET   /wp-json/<?php echo self::NS; ?>/options</code> — cidades, bairros, tipos disponíveis</li>
                <li><code>GET   /wp-json/<?php echo self::NS; ?>/properties/{id}</code> — status e links do imóvel</li>
            </ul>
            <p>Todos os endpoints exigem o header <code>Authorization: Bearer {chave}</code>.</p>
        </div>
        <?php
    }

    // -------------------------------------------------------------------------
    // Rotas REST
    // -------------------------------------------------------------------------

    public function register_routes(): void {
        register_rest_route(self::NS, '/properties', [
            'methods'             => 'POST',
            'callback'            => [$this, 'create_property'],
            'permission_callback' => [$this, 'authenticate'],
        ]);

        register_rest_route(self::NS, '/properties/(?P<id>\d+)/media', [
            'methods'             => 'POST',
            'callback'            => [$this, 'upload_media'],
            'permission_callback' => [$this, 'authenticate'],
            'args'                => ['id' => ['validate_callback' => fn($v) => is_numeric($v)]],
        ]);

        register_rest_route(self::NS, '/options', [
            'methods'             => 'GET',
            'callback'            => [$this, 'get_options'],
            'permission_callback' => [$this, 'authenticate'],
        ]);

        register_rest_route(self::NS, '/properties/(?P<id>\d+)', [
            'methods'             => 'GET',
            'callback'            => [$this, 'get_property'],
            'permission_callback' => [$this, 'authenticate'],
            'args'                => ['id' => ['validate_callback' => fn($v) => is_numeric($v)]],
        ]);
    }

    // -------------------------------------------------------------------------
    // Autenticação
    // -------------------------------------------------------------------------

    public function authenticate(WP_REST_Request $request): bool|WP_Error {
        $stored = get_option(self::OPTION_KEY, '');
        if (empty($stored)) {
            return new WP_Error('api_disabled', 'API key não configurada', ['status' => 503]);
        }
        $header = $request->get_header('Authorization') ?? '';
        if (!preg_match('/^Bearer\s+(\S+)$/i', $header, $m)) {
            return new WP_Error('unauthorized', 'Header Authorization ausente ou inválido', ['status' => 401]);
        }
        if (!hash_equals($stored, $m[1])) {
            return new WP_Error('forbidden', 'Chave de API inválida', ['status' => 403]);
        }
        return true;
    }

    // -------------------------------------------------------------------------
    // POST /properties — cria imóvel
    // -------------------------------------------------------------------------

    public function create_property(WP_REST_Request $request): WP_REST_Response|WP_Error {
        $p = $request->get_json_params() ?: $request->get_params();

        if (empty($p['titulo'])) {
            return new WP_Error('missing_field', 'Campo "titulo" é obrigatório', ['status' => 400]);
        }

        $post_status = !empty($p['publicar']) ? 'publish' : 'draft';
        $content     = $this->build_content($p);

        $post_id = wp_insert_post([
            'post_title'   => sanitize_text_field($p['titulo']),
            'post_content' => wp_kses_post($content),
            'post_status'  => $post_status,
            'post_type'    => self::POST_TYPE,
            'post_author'  => 1,
        ], true);

        if (is_wp_error($post_id)) {
            return new WP_Error('insert_failed', $post_id->get_error_message(), ['status' => 500]);
        }

        // Meta fields numéricos — Houzez
        $meta_map = [
            'preco'        => 'fave_property_price',
            'quartos'      => 'fave_property_bedrooms',
            'suites'       => 'fave_property_rooms',
            'banheiros'    => 'fave_property_bathrooms',
            'vagas'        => 'fave_property_garage',
            'area_m2'      => 'fave_property_size',
            'area_terreno' => 'fave_property_land',
            'ano_construcao'=> 'fave_property_year',
            'condominio'   => 'fave_property_condominium',
        ];
        foreach ($meta_map as $field => $meta_key) {
            if (!empty($p[$field])) {
                update_post_meta($post_id, $meta_key, sanitize_text_field((string) $p[$field]));
            }
        }

        // Sufixo de preço: "/mês" para aluguel, vazio para venda
        $operacao = strtolower(trim($p['operacao'] ?? ''));
        $postfix  = (str_contains($operacao, 'aluguel') || str_contains($operacao, 'locacao') || str_contains($operacao, 'locação')) ? '/mês' : '';
        update_post_meta($post_id, 'fave_property_price_postfix', $postfix);

        // Campos customizados do Mercadoi
        $custom = [
            'estagio_imovel' => 'estagio-da-obra-imóvel',
            'andar'          => 'no-térreo',
            'elevador'       => 'tem-elevador',
        ];
        foreach ($custom as $field => $meta_key) {
            if (!empty($p[$field])) {
                update_post_meta($post_id, $meta_key, sanitize_text_field($p[$field]));
            }
        }

        // Faz-parceria: sempre "A combinar"
        update_post_meta($post_id, 'faz-parceria', 'A combinar');

        // Descrição no campo próprio do tema
        update_post_meta($post_id, 'prop_des', wp_kses_post($content));

        // Não exibir contato (equivale a fave_agent_display_option = 2)
        update_post_meta($post_id, 'fave_agent_display_option', '2');

        // Taxonomias
        $this->set_taxonomies($post_id, $p);

        // Localização no mapa (Nominatim / OpenStreetMap — gratuito, sem API key)
        $this->geocode_and_save($post_id, $p);

        return rest_ensure_response([
            'id'          => $post_id,
            'url_admin'   => admin_url("post.php?post={$post_id}&action=edit"),
            'url_publica' => get_permalink($post_id) ?: '',
            'status'      => $post_status,
        ]);
    }

    // -------------------------------------------------------------------------
    // POST /properties/{id}/media — sobe imagens
    // -------------------------------------------------------------------------

    public function upload_media(WP_REST_Request $request): WP_REST_Response|WP_Error {
        $post_id = (int) $request->get_param('id');

        if (!get_post($post_id) || get_post_type($post_id) !== self::POST_TYPE) {
            return new WP_Error('not_found', 'Imóvel não encontrado', ['status' => 404]);
        }

        $files = $request->get_file_params();
        if (empty($files)) {
            return new WP_Error('no_files', 'Nenhum arquivo enviado', ['status' => 400]);
        }

        require_once ABSPATH . 'wp-admin/includes/file.php';
        require_once ABSPATH . 'wp-admin/includes/media.php';
        require_once ABSPATH . 'wp-admin/includes/image.php';

        $uploaded_ids = [];
        $errors       = [];

        foreach ($files as $key => $file) {
            // Suporte a múltiplos arquivos no mesmo campo (files[])
            if (is_array($file['name'])) {
                $count = count($file['name']);
                for ($i = 0; $i < $count; $i++) {
                    $single = [
                        'name'     => $file['name'][$i],
                        'type'     => $file['type'][$i],
                        'tmp_name' => $file['tmp_name'][$i],
                        'error'    => $file['error'][$i],
                        'size'     => $file['size'][$i],
                    ];
                    $result = $this->handle_upload($single, $post_id);
                    is_wp_error($result)
                        ? $errors[]       = $file['name'][$i] . ': ' . $result->get_error_message()
                        : $uploaded_ids[] = $result;
                }
            } else {
                $result = $this->handle_upload($file, $post_id);
                is_wp_error($result)
                    ? $errors[]       = $file['name'] . ': ' . $result->get_error_message()
                    : $uploaded_ids[] = $result;
            }
        }

        if (!empty($uploaded_ids)) {
            // Acumula IDs em fave_property_images (Houzez)
            $existing = get_post_meta($post_id, 'fave_property_images', true);
            $all_ids  = $existing ? array_filter(explode(',', $existing)) : [];
            $all_ids  = array_merge($all_ids, array_map('strval', $uploaded_ids));
            update_post_meta($post_id, 'fave_property_images', implode(',', $all_ids));

            // Define thumbnail se ainda não tiver
            if (!has_post_thumbnail($post_id)) {
                set_post_thumbnail($post_id, $uploaded_ids[0]);
            }
        }

        return rest_ensure_response([
            'sucesso'           => count($uploaded_ids),
            'arquivos_enviados' => $uploaded_ids,
            'erros'             => $errors,
        ]);
    }

    private function handle_upload(array $file, int $post_id): int|WP_Error {
        $_FILES['_bmapi_upload'] = $file;
        $attachment_id = media_handle_upload('_bmapi_upload', $post_id);
        unset($_FILES['_bmapi_upload']);
        return $attachment_id;
    }

    // -------------------------------------------------------------------------
    // GET /options — retorna cidades, bairros, tipos disponíveis
    // -------------------------------------------------------------------------

    public function get_options(WP_REST_Request $request): WP_REST_Response {
        $map = [
            'tipos'   => 'property-type',
            'status'  => 'property-status',
            'cidades' => 'property-city',
            'bairros' => 'property-area',
        ];

        $options = [];
        foreach ($map as $key => $taxonomy) {
            if (!taxonomy_exists($taxonomy)) {
                $options[$key] = [];
                continue;
            }
            $terms = get_terms([
                'taxonomy'   => $taxonomy,
                'hide_empty' => false,
                'number'     => 500,
                'orderby'    => 'name',
            ]);
            $options[$key] = is_wp_error($terms) ? [] : array_map(
                fn($t) => ['id' => $t->term_id, 'nome' => $t->name, 'slug' => $t->slug],
                $terms
            );
        }

        return rest_ensure_response($options);
    }

    // -------------------------------------------------------------------------
    // GET /properties/{id} — status e links
    // -------------------------------------------------------------------------

    public function get_property(WP_REST_Request $request): WP_REST_Response|WP_Error {
        $post_id = (int) $request->get_param('id');
        $post    = get_post($post_id);

        if (!$post || $post->post_type !== self::POST_TYPE) {
            return new WP_Error('not_found', 'Imóvel não encontrado', ['status' => 404]);
        }

        return rest_ensure_response([
            'id'          => $post_id,
            'titulo'      => $post->post_title,
            'status'      => $post->post_status,
            'url_admin'   => admin_url("post.php?post={$post_id}&action=edit"),
            'url_publica' => get_permalink($post_id) ?: '',
        ]);
    }

    // -------------------------------------------------------------------------
    // Helpers
    // -------------------------------------------------------------------------

    // -------------------------------------------------------------------------
    // Geocoding via Nominatim (OpenStreetMap) — sem API key, gratuito
    // -------------------------------------------------------------------------

    private function geocode_and_save(int $post_id, array $p): void {
        // Monta query: "Rua X 123, Bairro, Cidade, Brasil" ou "Bairro, Cidade, Brasil"
        $parts = array_filter([
            !empty($p['endereco']) ? sanitize_text_field($p['endereco']) : '',
            !empty($p['bairro'])   ? sanitize_text_field($p['bairro'])   : '',
            !empty($p['cidade'])   ? sanitize_text_field($p['cidade'])   : '',
            'Brasil',
        ]);

        if (count($parts) < 2) return; // Sem dados suficientes para geocodificar

        $query    = implode(', ', $parts);
        $endpoint = 'https://nominatim.openstreetmap.org/search?' . http_build_query([
            'q'              => $query,
            'format'         => 'json',
            'limit'          => 1,
            'addressdetails' => 0,
        ]);

        $response = wp_remote_get($endpoint, [
            'timeout'    => 8,
            'user-agent' => 'BotMercadoiAPI/1.0 (WordPress plugin; contact@mercadoi.com.br)',
            'headers'    => ['Accept-Language' => 'pt-BR,pt;q=0.9'],
        ]);

        if (is_wp_error($response)) return;

        $body = json_decode(wp_remote_retrieve_body($response), true);
        if (empty($body[0]['lat']) || empty($body[0]['lon'])) return;

        $lat = (float) $body[0]['lat'];
        $lng = (float) $body[0]['lon'];

        // Houzez armazena como "lat,lng" em fave_property_location
        update_post_meta($post_id, 'fave_property_location', "{$lat},{$lng}");
        update_post_meta($post_id, 'fave_property_map_address', sanitize_text_field($query));
        update_post_meta($post_id, 'fave_property_map_zoom',   '15');
    }

    private function set_taxonomies(int $post_id, array $p): void {
        $tax_map = [
            'tipo_imovel' => 'property-type',
            'operacao'    => 'property-status',
            'cidade'      => 'property-city',
            'bairro'      => 'property-area',
        ];
        foreach ($tax_map as $field => $taxonomy) {
            if (empty($p[$field]) || !taxonomy_exists($taxonomy)) continue;
            $term = get_term_by('name', $p[$field], $taxonomy)
                 ?: get_term_by('slug', sanitize_title($p[$field]), $taxonomy);
            if ($term) {
                wp_set_object_terms($post_id, [$term->term_id], $taxonomy, false);
            }
        }
    }

    private function build_content(array $p): string {
        $desc     = $p['descricao'] ?? '';
        $url_pub  = $this->normalize_url($p['url_publicacao'] ?? '');
        $whatsapp = $this->normalize_url($p['whatsapp_url'] ?? '');
        $instagram= $this->normalize_url($p['instagram_url'] ?? '');

        $icons = '';
        if ($url_pub) {
            $icons .= sprintf(
                '<a href="%s" target="_blank" rel="noopener"><img src="https://mercadoi.com.br/ver-video-mi/" width="120" height="120" /></a>',
                esc_url($url_pub)
            );
        }
        if ($whatsapp) {
            $icons .= sprintf(
                '<a href="%s" target="_blank" rel="noopener"><img src="https://mercadoi.com.br/whatsapp-mi/" width="75" height="75" /></a>',
                esc_url($whatsapp)
            );
        }
        if ($instagram) {
            $icons .= sprintf(
                '<a href="%s" target="_blank" rel="noopener"><img src="https://mercadoi.com.br/instagram-mi/" width="75" height="75" /></a>',
                esc_url($instagram)
            );
        }

        return $desc . ($icons ? "\n\n<pre>{$icons}</pre>" : '');
    }

    private function normalize_url(string $v): string {
        $v = trim($v);
        if (!$v) return '';
        if (str_starts_with($v, 'wa.me/') || str_starts_with($v, 'instagram.com/')) {
            $v = 'https://' . $v;
        }
        return filter_var($v, FILTER_VALIDATE_URL) ? $v : '';
    }
}

new BotMercadoiAPI();
