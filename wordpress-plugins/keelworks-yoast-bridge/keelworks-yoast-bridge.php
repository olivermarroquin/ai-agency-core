<?php
/**
 * Plugin Name: Keelworks Yoast Bridge
 * Plugin URI:  https://keelworks.ai/
 * Description: Adds a Basic-Auth REST endpoint so an external publish script can write Yoast SEO meta (title, description, focus keyword) for a page or post. Mirrors the Keelworks AIOSEO Bridge pattern for sites running Yoast instead of AIOSEO. Only Editors and Administrators can call the endpoint.
 * Version:     1.0.0
 * Author:      Keelworks
 * Author URI:  https://keelworks.ai/
 * License:     MIT
 * Requires at least: 5.7
 * Requires PHP: 7.4
 *
 * The endpoint:
 *   POST /wp-json/keelworks/v1/yoast-meta/<post_id>
 *
 * Body (JSON):
 *   {
 *     "title":           "SEO title (string, optional)",
 *     "description":     "Meta description (string, optional)",
 *     "focus_keyphrase": "Focus keyword (string, optional)"
 *   }
 *
 * Auth: WordPress Basic Auth via Application Password. Caller must be an
 * Administrator or Editor (capability check: edit_others_pages).
 *
 * Behavior: writes to wp_postmeta using Yoast's standard meta keys:
 *   _yoast_wpseo_title    ← title
 *   _yoast_wpseo_metadesc ← description
 *   _yoast_wpseo_focuskw  ← focus_keyphrase
 *
 * Unlike AIOSEO (which uses a custom table), Yoast stores everything in
 * wp_postmeta, so this plugin uses the standard update_post_meta() API.
 *
 * If Yoast SEO is not installed/active, the endpoint returns 503 so the
 * caller can fall back to manual entry.
 */

// Exit if accessed directly.
if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

add_action( 'rest_api_init', function () {
	register_rest_route(
		'keelworks/v1',
		'/yoast-meta/(?P<id>\d+)',
		array(
			'methods'             => 'POST',
			'callback'            => 'keelworks_yoast_bridge_handle_post',
			'permission_callback' => function () {
				return current_user_can( 'edit_others_pages' );
			},
			'args' => array(
				'id' => array(
					'validate_callback' => function ( $param ) {
						return is_numeric( $param ) && intval( $param ) > 0;
					},
				),
			),
		)
	);

	// GET all post-meta for a page (diagnostic / key discovery).
	register_rest_route(
		'keelworks/v1',
		'/post-meta/(?P<id>\d+)',
		array(
			'methods'             => 'GET',
			'callback'            => 'keelworks_post_meta_read',
			'permission_callback' => function () {
				return current_user_can( 'edit_others_pages' );
			},
			'args' => array(
				'id' => array(
					'validate_callback' => function ( $param ) {
						return is_numeric( $param ) && intval( $param ) > 0;
					},
				),
			),
		)
	);

	// POST page-options meta (theme display settings).
	register_rest_route(
		'keelworks/v1',
		'/page-options/(?P<id>\d+)',
		array(
			'methods'             => 'POST',
			'callback'            => 'keelworks_page_options_write',
			'permission_callback' => function () {
				return current_user_can( 'edit_others_pages' );
			},
			'args' => array(
				'id' => array(
					'validate_callback' => function ( $param ) {
						return is_numeric( $param ) && intval( $param ) > 0;
					},
				),
			),
		)
	);
} );

/**
 * Handle the POST /keelworks/v1/yoast-meta/<id> request.
 *
 * @param WP_REST_Request $request
 * @return WP_REST_Response|WP_Error
 */
function keelworks_yoast_bridge_handle_post( $request ) {
	$post_id = intval( $request['id'] );
	$post    = get_post( $post_id );
	if ( ! $post ) {
		return new WP_Error(
			'keelworks_post_not_found',
			"No post found with ID {$post_id}.",
			array( 'status' => 404 )
		);
	}

	// Confirm Yoast SEO is active by checking for the WPSEO_VERSION constant
	// that Yoast defines on load.
	if ( ! defined( 'WPSEO_VERSION' ) ) {
		return new WP_Error(
			'keelworks_yoast_not_installed',
			'Yoast SEO does not appear to be active on this site (WPSEO_VERSION not defined). Install and activate Yoast SEO, then try again.',
			array( 'status' => 503 )
		);
	}

	$body = $request->get_json_params();
	if ( ! is_array( $body ) ) {
		return new WP_Error(
			'keelworks_invalid_body',
			'Request body must be a JSON object.',
			array( 'status' => 400 )
		);
	}

	$title           = isset( $body['title'] ) ? sanitize_text_field( $body['title'] ) : null;
	$description     = isset( $body['description'] ) ? sanitize_text_field( $body['description'] ) : null;
	$focus_keyphrase = isset( $body['focus_keyphrase'] ) ? sanitize_text_field( $body['focus_keyphrase'] ) : null;

	$wrote = array();

	if ( $title !== null ) {
		update_post_meta( $post_id, '_yoast_wpseo_title', $title );
		$wrote['title'] = $title;
	}
	if ( $description !== null ) {
		update_post_meta( $post_id, '_yoast_wpseo_metadesc', $description );
		$wrote['description'] = $description;
	}
	if ( $focus_keyphrase !== null ) {
		update_post_meta( $post_id, '_yoast_wpseo_focuskw', $focus_keyphrase );
		$wrote['focus_keyphrase'] = $focus_keyphrase;
	}

	if ( empty( $wrote ) ) {
		return new WP_Error(
			'keelworks_nothing_to_write',
			'No recognized fields in the request body (expected: title, description, focus_keyphrase).',
			array( 'status' => 400 )
		);
	}

	return rest_ensure_response( array(
		'ok'             => true,
		'post_id'        => $post_id,
		'action'         => 'wrote',
		'wrote'          => $wrote,
		'yoast_version'  => WPSEO_VERSION,
		'plugin_version' => '1.0.0',
	) );
}

/**
 * GET /keelworks/v1/post-meta/<id> — read all post-meta for key discovery.
 */
function keelworks_post_meta_read( $request ) {
	$post_id = intval( $request['id'] );
	$post    = get_post( $post_id );
	if ( ! $post ) {
		return new WP_Error( 'not_found', "No post {$post_id}.", array( 'status' => 404 ) );
	}
	$all_meta = get_post_meta( $post_id );
	// Flatten single-value arrays for readability.
	$flat = array();
	foreach ( $all_meta as $key => $values ) {
		$flat[ $key ] = ( count( $values ) === 1 ) ? $values[0] : $values;
	}
	return rest_ensure_response( array( 'post_id' => $post_id, 'meta' => $flat ) );
}

/**
 * POST /keelworks/v1/page-options/<id> — write theme page-display meta.
 *
 * Body (JSON): arbitrary key-value pairs that map to post-meta keys.
 * The publish script discovers the correct keys via the GET /post-meta
 * endpoint, then writes them here.
 *
 * Example: {"trx_addons_options_post_header": "hide", ...}
 */
function keelworks_page_options_write( $request ) {
	$post_id = intval( $request['id'] );
	$post    = get_post( $post_id );
	if ( ! $post ) {
		return new WP_Error( 'not_found', "No post {$post_id}.", array( 'status' => 404 ) );
	}

	$body = $request->get_json_params();
	if ( ! is_array( $body ) || empty( $body ) ) {
		return new WP_Error( 'invalid_body', 'Body must be a non-empty JSON object.', array( 'status' => 400 ) );
	}

	$wrote = array();
	foreach ( $body as $key => $value ) {
		// Sanitize key — only allow word chars, hyphens, and the leading underscore.
		$safe_key = preg_replace( '/[^a-zA-Z0-9_\-]/', '', $key );
		if ( $safe_key !== $key || strlen( $safe_key ) < 2 ) {
			continue; // skip suspicious keys
		}
		update_post_meta( $post_id, $safe_key, sanitize_text_field( $value ) );
		$wrote[ $safe_key ] = $value;
	}

	if ( empty( $wrote ) ) {
		return new WP_Error( 'nothing_written', 'No valid keys to write.', array( 'status' => 400 ) );
	}

	return rest_ensure_response( array(
		'ok'             => true,
		'post_id'        => $post_id,
		'action'         => 'wrote',
		'wrote'          => $wrote,
		'plugin_version' => '1.0.0',
	) );
}
