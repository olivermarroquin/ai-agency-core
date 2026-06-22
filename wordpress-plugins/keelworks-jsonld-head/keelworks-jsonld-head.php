<?php
/**
 * Plugin Name: Keelworks JSON-LD Head Injection
 * Description: Reads per-page post meta '_kw_jsonld' and outputs it as a
 *              <script type="application/ld+json"> block in wp_head.
 *              Client-agnostic: works on any WordPress site, any page.
 *              Set the meta via WP REST API (meta._kw_jsonld on POST/PUT
 *              to /wp-json/wp/v2/pages/{id}) or via update_post_meta().
 * Version:     1.0.1
 * Author:      Keelworks
 * License:     Proprietary
 *
 * Usage as mu-plugin: copy this file to wp-content/mu-plugins/keelworks-jsonld-head.php
 * Usage as regular plugin: copy the folder to wp-content/plugins/ and activate.
 */

if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

/**
 * Register '_kw_jsonld' so it is readable and writable via the WP REST API
 * on both posts and pages.
 */
add_action( 'init', function () {
    $args = [
        'type'              => 'string',
        'single'            => true,
        'show_in_rest'      => true,
        'sanitize_callback' => function ( $value ) {
            // Allow valid JSON-LD only. Must be parseable JSON.
            if ( empty( $value ) ) {
                return '';
            }
            $decoded = json_decode( $value, true );
            if ( json_last_error() !== JSON_ERROR_NONE ) {
                return ''; // silently reject non-JSON
            }
            // Re-encode to normalize. JSON_HEX_TAG escapes </>  to
            // \u003C/\u003E so a stored </script> cannot break out of
            // the JSON-LD script block when output in wp_head.
            return wp_json_encode( $decoded, JSON_HEX_TAG | JSON_HEX_AMP | JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE );
        },
        'auth_callback'     => function () {
            return current_user_can( 'edit_pages' );
        },
    ];

    register_post_meta( 'page', '_kw_jsonld', $args );
    register_post_meta( 'post', '_kw_jsonld', $args );
} );

/**
 * Output the JSON-LD in <head> for the current singular page/post.
 */
add_action( 'wp_head', function () {
    if ( ! is_singular() ) {
        return;
    }

    $post_id = get_queried_object_id();
    if ( ! $post_id ) {
        return;
    }

    $jsonld = get_post_meta( $post_id, '_kw_jsonld', true );
    if ( empty( $jsonld ) ) {
        return;
    }

    // Validate it's still parseable JSON before outputting
    $decoded = json_decode( $jsonld, true );
    if ( json_last_error() !== JSON_ERROR_NONE ) {
        return;
    }

    // Output as a clean JSON-LD script tag. JSON_HEX_TAG + JSON_HEX_AMP
    // prevent </script> breakout even if a stored value contains it.
    $output = wp_json_encode( $decoded, JSON_HEX_TAG | JSON_HEX_AMP | JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT );
    echo "\n<!-- Keelworks JSON-LD Head Injection v1.0.1 -->\n";
    echo '<script type="application/ld+json">' . "\n";
    echo $output . "\n";
    echo '</script>' . "\n";
}, 5 ); // priority 5 = before most plugins (AIOSEO is typically 10+)
