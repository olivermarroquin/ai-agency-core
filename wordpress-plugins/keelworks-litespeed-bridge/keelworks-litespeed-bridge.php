<?php
/**
 * Plugin Name: Keelworks LiteSpeed Bridge
 * Plugin URI:  https://keelworks.ai/
 * Description: Basic-Auth REST endpoints for managing LiteSpeed Cache exclusion URIs and purging specific URLs. Bridges the gap between external automation scripts and LiteSpeed Cache's wp-admin-only configuration. Only Administrators can call these endpoints.
 * Version:     1.0.0
 * Author:      Keelworks
 * Author URI:  https://keelworks.ai/
 * License:     MIT
 * Requires at least: 5.7
 * Requires PHP: 7.4
 *
 * Endpoints:
 *
 *   GET  /wp-json/keelworks/v1/litespeed-cache-exc
 *     Returns the current "Do Not Cache URIs" patterns from LiteSpeed Cache.
 *
 *   POST /wp-json/keelworks/v1/litespeed-cache-exc
 *     Body: {"patterns": ["/page-sitemap.xml", "/sitemap_index.xml"]}
 *     Adds patterns to the "Do Not Cache URIs" list (idempotent — skips duplicates).
 *
 *   POST /wp-json/keelworks/v1/litespeed-purge
 *     Body: {"urls": ["https://example.com/page-sitemap.xml"]}  — purge specific URLs
 *     Body: {"purge_all": true}                                 — purge entire cache
 *
 * Auth: WordPress Basic Auth via Application Password. Caller must be an
 * Administrator (capability check: manage_options).
 *
 * LiteSpeed Cache 4.x+ stores "Do Not Cache URIs" in wp_options under the key
 * `litespeed.conf.cache-exc` as a newline-separated string. This plugin reads
 * and writes that option directly.
 */

// Exit if accessed directly.
if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

add_action( 'rest_api_init', function () {

	// GET — read current "Do Not Cache URIs"
	register_rest_route(
		'keelworks/v1',
		'/litespeed-cache-exc',
		array(
			'methods'             => 'GET',
			'callback'            => 'keelworks_lscache_exc_get',
			'permission_callback' => function () {
				return current_user_can( 'manage_options' );
			},
		)
	);

	// POST — add patterns to "Do Not Cache URIs"
	register_rest_route(
		'keelworks/v1',
		'/litespeed-cache-exc',
		array(
			'methods'             => 'POST',
			'callback'            => 'keelworks_lscache_exc_post',
			'permission_callback' => function () {
				return current_user_can( 'manage_options' );
			},
		)
	);

	// POST — purge specific URLs or entire cache
	register_rest_route(
		'keelworks/v1',
		'/litespeed-purge',
		array(
			'methods'             => 'POST',
			'callback'            => 'keelworks_lscache_purge',
			'permission_callback' => function () {
				return current_user_can( 'manage_options' );
			},
		)
	);
} );

/**
 * GET /keelworks/v1/litespeed-cache-exc
 *
 * Returns the current "Do Not Cache URIs" patterns.
 */
function keelworks_lscache_exc_get( $request ) {
	$option_key = 'litespeed.conf.cache-exc';
	$raw        = get_option( $option_key, '' );
	$patterns   = array_values( array_filter( array_map( 'trim', explode( "\n", $raw ) ) ) );

	return rest_ensure_response( array(
		'ok'         => true,
		'option_key' => $option_key,
		'raw_value'  => $raw,
		'patterns'   => $patterns,
		'count'      => count( $patterns ),
	) );
}

/**
 * POST /keelworks/v1/litespeed-cache-exc
 *
 * Adds patterns to the "Do Not Cache URIs" list. Idempotent — existing
 * patterns are not duplicated.
 *
 * Body: {"patterns": ["/page-sitemap.xml", "/sitemap_index.xml", ...]}
 */
function keelworks_lscache_exc_post( $request ) {
	$body = $request->get_json_params();

	if ( ! is_array( $body ) || ! isset( $body['patterns'] ) || ! is_array( $body['patterns'] ) ) {
		return new WP_Error(
			'keelworks_invalid_body',
			'Request body must be {"patterns": ["...", "..."]}.',
			array( 'status' => 400 )
		);
	}

	$option_key   = 'litespeed.conf.cache-exc';
	$raw          = get_option( $option_key, '' );
	$existing     = array_filter( array_map( 'trim', explode( "\n", $raw ) ) );
	$new_patterns = array_map( 'sanitize_text_field', $body['patterns'] );

	$added = array();
	foreach ( $new_patterns as $p ) {
		$p = trim( $p );
		if ( $p !== '' && ! in_array( $p, $existing, true ) ) {
			$existing[] = $p;
			$added[]    = $p;
		}
	}

	$new_value = implode( "\n", $existing );
	update_option( $option_key, $new_value );

	return rest_ensure_response( array(
		'ok'             => true,
		'added'          => $added,
		'already_present' => count( $new_patterns ) - count( $added ),
		'total_patterns' => count( $existing ),
		'all_patterns'   => array_values( $existing ),
		'plugin_version' => '1.0.0',
	) );
}

/**
 * POST /keelworks/v1/litespeed-purge
 *
 * Purges specific URLs from the LiteSpeed edge cache, or the entire cache.
 *
 * Body options:
 *   {"urls": ["https://example.com/page-sitemap.xml"]}
 *   {"purge_all": true}
 *   {"urls": [...], "purge_all": true}   — both are honoured
 */
function keelworks_lscache_purge( $request ) {
	$body   = $request->get_json_params();
	$purged = array();
	$errors = array();

	if ( isset( $body['urls'] ) && is_array( $body['urls'] ) ) {
		foreach ( $body['urls'] as $url ) {
			$url = esc_url_raw( $url );
			if ( empty( $url ) ) {
				continue;
			}
			// LiteSpeed Cache ≥4.0 action hook for single-URL purge.
			do_action( 'litespeed_purge_url', $url );
			$purged[] = $url;
		}
	}

	if ( ! empty( $body['purge_all'] ) ) {
		do_action( 'litespeed_purge_all' );
		$purged[] = '__ALL__';
	}

	if ( empty( $purged ) ) {
		return new WP_Error(
			'keelworks_nothing_to_purge',
			'No URLs or purge_all flag provided. Body: {"urls": [...]} or {"purge_all": true}.',
			array( 'status' => 400 )
		);
	}

	return rest_ensure_response( array(
		'ok'             => true,
		'purged'         => $purged,
		'count'          => count( $purged ),
		'plugin_version' => '1.0.0',
	) );
}
