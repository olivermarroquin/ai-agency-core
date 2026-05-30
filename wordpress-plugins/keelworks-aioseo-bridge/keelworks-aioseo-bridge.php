<?php
/**
 * Plugin Name: Keelworks AIOSEO Bridge
 * Plugin URI:  https://keelworks.ai/
 * Description: Adds a Basic-Auth REST endpoint so an external publish script can write AIOSEO meta (title, description, focus keyword, additional keywords) for a page or post. Bridges the gap between WordPress application passwords and AIOSEO's cookie-only admin save path. Only Editors and Administrators can call the endpoint.
 * Version:     1.0.0
 * Author:      Keelworks
 * Author URI:  https://keelworks.ai/
 * License:     MIT
 * Requires at least: 5.7
 * Requires PHP: 7.4
 *
 * The endpoint:
 *   POST /wp-json/keelworks/v1/aioseo-meta/<post_id>
 *
 * Body (JSON):
 *   {
 *     "title":               "Page title for SEO (string, optional)",
 *     "description":         "Meta description (string, optional)",
 *     "focus_keyphrase":     "Focus keyword (string, optional)",
 *     "additional_keywords": ["keyword 1", "keyword 2"]   // array of strings, optional
 *   }
 *
 * Auth: WordPress Basic Auth via Application Password. Caller must be an
 * Administrator or Editor (capability check: edit_others_pages).
 *
 * Behavior: upserts a row in the aioseo_posts table for the given post_id.
 * If AIOSEO is not installed or its table is missing, the endpoint returns
 * a clear 503 error so the caller can fall back to manual entry.
 */

// Exit if accessed directly.
if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

add_action( 'rest_api_init', function () {
	register_rest_route(
		'keelworks/v1',
		'/aioseo-meta/(?P<id>\d+)',
		array(
			'methods'             => 'POST',
			'callback'            => 'keelworks_aioseo_bridge_handle_post',
			'permission_callback' => function () {
				// Only Admins and Editors can write AIOSEO meta via this route.
				// edit_others_pages is the right capability — it's granted to
				// Administrator and Editor roles by default, denied to Author/Contributor.
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
 * Handle the POST /keelworks/v1/aioseo-meta/<id> request.
 *
 * @param WP_REST_Request $request
 * @return WP_REST_Response|WP_Error
 */
function keelworks_aioseo_bridge_handle_post( $request ) {
	global $wpdb;

	$post_id = intval( $request['id'] );
	$post    = get_post( $post_id );
	if ( ! $post ) {
		return new WP_Error(
			'keelworks_post_not_found',
			"No post found with ID {$post_id}.",
			array( 'status' => 404 )
		);
	}

	$table = $wpdb->prefix . 'aioseo_posts';

	// Confirm the AIOSEO table exists before we touch it.
	$exists = $wpdb->get_var(
		$wpdb->prepare( 'SHOW TABLES LIKE %s', $table )
	);
	if ( $exists !== $table ) {
		return new WP_Error(
			'keelworks_aioseo_not_installed',
			'AIOSEO does not appear to be installed on this site (aioseo_posts table is missing). Install AIOSEO and try again, or skip the meta auto-population.',
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

	$additional = array();
	if ( isset( $body['additional_keywords'] ) && is_array( $body['additional_keywords'] ) ) {
		foreach ( $body['additional_keywords'] as $kw ) {
			if ( is_string( $kw ) && trim( $kw ) !== '' ) {
				$additional[] = sanitize_text_field( $kw );
			}
		}
	}

	// AIOSEO stores keyphrases as a JSON-encoded structure. Match the shape we
	// captured from the wp-admin sidebar save so AIOSEO's Vue UI re-hydrates
	// correctly when an operator opens the page later.
	$keyphrases_struct = array(
		'focus' => array(
			'keyphrase' => $focus_keyphrase !== null ? $focus_keyphrase : '',
			'score'     => 0,
			'analysis'  => new stdClass(),
		),
		'additional' => array(),
	);
	foreach ( $additional as $kw ) {
		$keyphrases_struct['additional'][] = array(
			'keyphrase' => $kw,
			'score'     => 0,
			'analysis'  => new stdClass(),
		);
	}
	$keyphrases_json = wp_json_encode( $keyphrases_struct );

	// Build the row data. Only include columns the caller actually provided so
	// we don't clobber existing AIOSEO values with nulls.
	$now    = current_time( 'mysql', true ); // GMT timestamp
	$update = array(
		'updated' => $now,
	);
	if ( $title !== null ) {
		$update['title'] = $title;
	}
	if ( $description !== null ) {
		$update['description'] = $description;
	}
	// Always write keyphrases when the caller supplied any keyword field, so
	// the focus and additional set stay in sync.
	if ( $focus_keyphrase !== null || ! empty( $additional ) ) {
		$update['keyphrases'] = $keyphrases_json;
	}

	// Check if a row already exists for this post.
	$existing_id = $wpdb->get_var(
		$wpdb->prepare(
			"SELECT id FROM {$table} WHERE post_id = %d LIMIT 1",
			$post_id
		)
	);

	if ( $existing_id ) {
		$result = $wpdb->update(
			$table,
			$update,
			array( 'post_id' => $post_id )
		);
		$action_taken = 'updated';
	} else {
		// Insert needs post_id + created timestamp on top of $update.
		$insert            = $update;
		$insert['post_id'] = $post_id;
		$insert['created'] = $now;
		$result            = $wpdb->insert( $table, $insert );
		$action_taken      = 'inserted';
	}

	if ( $result === false ) {
		return new WP_Error(
			'keelworks_db_write_failed',
			'Failed to write AIOSEO row. DB error: ' . $wpdb->last_error,
			array( 'status' => 500 )
		);
	}

	return rest_ensure_response( array(
		'ok'        => true,
		'post_id'   => $post_id,
		'action'    => $action_taken,
		'wrote'     => array(
			'title'               => $title,
			'description'         => $description,
			'focus_keyphrase'     => $focus_keyphrase,
			'additional_keywords' => $additional,
		),
		'plugin_version' => '1.0.0',
	) );
}
