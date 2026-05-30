=== Keelworks AIOSEO Bridge ===
Contributors: keelworks
Tags: aioseo, rest-api, automation, headless
Requires at least: 5.7
Tested up to: 6.5
Requires PHP: 7.4
Stable tag: 1.0.0
License: MIT

A Basic-Auth REST endpoint that lets an external script write AIOSEO meta
(title, meta description, focus keyword, additional keywords) for any post
or page. Bridges WordPress application passwords with AIOSEO's cookie-only
admin save path.

== Description ==

By default, AIOSEO Free saves its meta fields via a legacy admin form that
requires a WordPress login cookie plus a nonce. WordPress application passwords
(used by external scripts and integrations) only work against REST endpoints,
which means an external script cannot populate AIOSEO meta without either
buying the Pro REST API addon or installing a bridge like this one.

This plugin adds one REST route:

    POST /wp-json/keelworks/v1/aioseo-meta/<post_id>

The route accepts a JSON body with title, description, focus_keyphrase, and
additional_keywords[]. It writes those values directly to the aioseo_posts
custom table. Only users with the edit_others_pages capability
(Administrator and Editor by default) can call it.

== Installation ==

1. wp-admin -> Plugins -> Add New -> Upload Plugin
2. Upload keelworks-aioseo-bridge.zip
3. Activate
4. Confirm the route exists by visiting (logged in):
   /wp-json/keelworks/v1
   You should see /aioseo-meta/(?P<id>\d+) listed.

== Usage example ==

    curl -X POST \
      -u "youruser:your-app-password" \
      -H "Content-Type: application/json" \
      -d '{"title":"...","description":"...","focus_keyphrase":"...","additional_keywords":["..."]}' \
      https://yoursite.com/wp-json/keelworks/v1/aioseo-meta/123

== Changelog ==

= 1.0.0 =
* Initial release. Single POST route, upserts aioseo_posts row.
