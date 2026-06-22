# BTF-1 Architecture Spec — Business-Type-Agnostic Factory

**Status:** DRAFT — awaiting operator review before implementation
**Created:** 2026-06-22
**Author:** Claude Code (BTF-1 producer session)
**Scope:** Wave 1 — stand up restaurant as the second business type alongside electrician

---

## 1. Problem Statement

The site-building factory (scaffolder + schema + keyword research + content templates + onboarding skill) currently works only for electrician businesses. Every layer — client config, page model, JSON-LD schema, keyword patterns, content sections, CTAs — silently assumes "local service business: service × city."

A restaurant breaks every one of those assumptions. The page model is not service × city; the schema type is `Restaurant`, not `LocalBusiness`; keywords are cuisine/dish/delivery patterns, not "service in city"; CTAs are "reserve" or "order," not "call now."

**Goal:** Make the factory type-parameterized so that `business_type` in the client config selects the right profile, and the engine renders any business type from config + profile alone — zero type-specific hardcoding in the engine.

---

## 2. Architecture: Three-Layer Split

```
┌─────────────────────────────────────────────────────────┐
│                    CLIENT CONFIG                         │
│  client-<slug>.json — per-client facts                  │
│  business_type field routes to the correct profile       │
│  Type-specific fields live in a keyed section            │
└──────────────────────┬──────────────────────────────────┘
                       │ loads
┌──────────────────────▼──────────────────────────────────┐
│                    TYPE PROFILE                           │
│  profiles/<business_type>/                               │
│  - page-model.json        (pages to build, their roles)  │
│  - schema-template.json   (JSON-LD shape + required fields)│
│  - keyword-research.json  (patterns, modifiers, intents)  │
│  - content-sections.json  (section types, CTA templates)  │
│  - config-schema.json     (required/optional client fields)│
│  One profile per business type. Restaurant ≠ electrician. │
└──────────────────────┬──────────────────────────────────┘
                       │ consumed by
┌──────────────────────▼──────────────────────────────────┐
│                   UNIVERSAL ENGINE                        │
│  scaffold-page.py (renamed from scaffold-core-30-page.py)│
│  - Reads client config → extracts business_type          │
│  - Loads the matching profile from profiles/<type>/      │
│  - Builds pages per the profile's page model             │
│  - Builds JSON-LD per the profile's schema template      │
│  - Selects content sections + CTAs per the profile       │
│  - ZERO type-specific conditionals in the engine         │
│  keelworks-jsonld-head.php — already fully agnostic      │
└─────────────────────────────────────────────────────────┘
```

### 2.1 Design Principle: Profile, Not If-Else

The engine MUST NOT contain `if business_type == "restaurant"` branches. Instead, each profile declares:
- What pages to build (and their data shape)
- What JSON-LD `@type` and properties to emit
- What keyword patterns to research
- What content sections each page has
- What CTA verbs and actions to use

The engine reads these declarations and renders them. Adding business type #3 means adding a new `profiles/<type>/` folder — zero engine changes.

---

## 3. Client Config Schema Changes

### 3.1 New Universal Fields

```jsonc
{
  // NEW — required, routes to profile
  "business_type": "restaurant",   // or "electrician", "plumber", etc.

  // NEW — optional, overrides @type in JSON-LD (profile has a default)
  "schema_type_override": null,

  // EXISTING — these stay universal (every business has them)
  "client_slug": "...",
  "name": "...",
  "owner_name": "...",
  "owner_title": "...",        // "Master Electrician" or "Owner/Chef" — type-neutral field name
  "website_url": "...",
  "phone_display": "...",
  "phone_tel": "...",
  "phone_e164": "...",
  "email": "...",
  "address": { ... },
  "geo": { ... },
  "hours": [ ... ],
  "primary_color": "...",
  // ... all existing brand/color/review fields stay
}
```

### 3.2 Type-Specific Fields — Keyed by `business_type`

Instead of polluting the root with fields that only matter for one type, type-specific data lives under a key matching the `business_type` value:

```jsonc
{
  "business_type": "restaurant",

  // Universal fields at root...

  // Type-specific section — key matches business_type
  "restaurant": {
    "cuisine_types": ["Burmese", "Thai", "Asian Fusion"],
    "primary_cuisine": "Burmese",
    "menu_url": "https://asiandelightbaltimore.com/menu",
    "menu_sections": [
      {
        "name": "Appetizers",
        "items": [
          { "name": "Tea Leaf Salad", "description": "...", "price": "$12" },
          { "name": "Samosa Soup", "description": "...", "price": "$8" }
        ]
      }
    ],
    "food_price_range": "$$",
    "dietary_options": ["vegetarian", "vegan", "gluten-free"],
    "accepts_reservations": true,
    "reservation_url": "https://asiandelightbaltimore.com/reserve",
    "accepts_online_orders": true,
    "order_url": "https://asiandelightbaltimore.com/order",
    "delivery_partners": ["DoorDash", "UberEats"],
    "ambiance": "Casual dining",
    "seating_capacity": 60,
    "alcohol_served": true,
    "parking": "Street parking + rear lot"
  }
}
```

For electrician, the type-specific section is:

```jsonc
{
  "business_type": "electrician",

  "electrician": {
    "license": {
      "category": "Master Electrician License",
      "issuer": "Commonwealth of Virginia, DPOR",
      "state": "Virginia",
      "state_full": "Commonwealth of Virginia"
    },
    "services": ["troubleshooting", "panel-upgrade", "ev-charger", ...],
    "service_area_cities": ["Vienna", "Fairfax", "McLean", ...],
    "utility_providers": ["Dominion Energy"],
    "emergency_dispatch": true,
    "dispatch_time_default": "45-minute"
  }
}
```

### 3.3 Migration Path for Existing Configs

Existing `client-ev-electric-services.json` and `client-s-and-h-contracting.json` get:
1. A `"business_type": "electrician"` field added at root
2. The `license` block MOVED under `"electrician": { "license": { ... } }`
3. Other electrician-specific fields (that currently live at root) relocated under `"electrician": {}`
4. All universal fields stay at root

This is a **data migration**, not a breaking change — the engine reads `business_type` and loads the type-specific section by key.

---

## 4. Type Profile Structure

Each profile lives in `repos/ai-agency-core/scripts/profiles/<business_type>/` and contains five files:

### 4.1 `page-model.json` — What pages to build

```jsonc
// profiles/restaurant/page-model.json
{
  "business_type": "restaurant",
  "page_model_name": "Restaurant Multi-Page",
  "description": "Tailored page set for a restaurant — NOT service × city",

  "pages": [
    {
      "slug": "home",
      "role": "homepage",
      "title_template": "{client_name} — {primary_cuisine} Restaurant in {city_name}",
      "description": "Main landing page. Hero + cuisine overview + featured dishes + reviews + location/hours CTA.",
      "sections": ["hero", "cuisine-overview", "featured-dishes", "reviews", "location-cta", "about-teaser"],
      "is_index": true
    },
    {
      "slug": "menu",
      "role": "menu",
      "title_template": "Menu — {client_name}",
      "description": "Full menu with sections, items, prices, dietary flags. Structured data: hasMenu.",
      "sections": ["menu-hero", "menu-sections", "dietary-info", "order-cta"]
    },
    {
      "slug": "about",
      "role": "about",
      "title_template": "About {client_name} — Our Story",
      "description": "Owner story, restaurant history, cuisine philosophy, team.",
      "sections": ["about-hero", "owner-story", "cuisine-philosophy", "team"]
    },
    {
      "slug": "location-hours",
      "role": "location",
      "title_template": "Location & Hours — {client_name}",
      "description": "Address, map embed, hours by day, parking, transit directions.",
      "sections": ["location-hero", "map-embed", "hours-table", "parking-transit", "reservation-cta"]
    },
    {
      "slug": "cuisine",
      "role": "cuisine-spotlight",
      "title_template": "{primary_cuisine} Cuisine in {city_name} — {client_name}",
      "description": "Deep dive on the cuisine tradition. Keyword play: '{cuisine} food near me.'",
      "sections": ["cuisine-hero", "cuisine-history", "signature-techniques", "featured-dishes", "order-cta"]
    },
    {
      "slug": "reserve-order",
      "role": "conversion",
      "title_template": "Reserve a Table or Order Online — {client_name}",
      "description": "Dual-CTA page: reservation form/link + online ordering links.",
      "sections": ["conversion-hero", "reservation-block", "order-block", "hours-summary", "contact-info"]
    }
  ]
}
```

Compare to the electrician page model (service × city matrix):

```jsonc
// profiles/electrician/page-model.json
{
  "business_type": "electrician",
  "page_model_name": "Core 30 — Service × City",
  "description": "30 pages from service slugs × city slugs matrix",

  "page_generation": "matrix",
  "matrix": {
    "axis_a": "services",
    "axis_b": "cities",
    "slug_template": "{service_slug}-{city_slug}",
    "title_template": "{service_name} in {city_name}, {city_state} — {client_name}"
  },

  "pages": []
}
```

**Key distinction:** Restaurant pages are a fixed list. Electrician pages are a matrix product. The engine handles both: if `page_generation == "matrix"`, it expands the matrix; if `pages[]` is a list, it iterates the list.

### 4.2 `schema-template.json` — JSON-LD shape

```jsonc
// profiles/restaurant/schema-template.json
{
  "business_type": "restaurant",
  "primary_type": "Restaurant",
  "additional_types": [],

  "business_node": {
    "@type": "Restaurant",
    "required_fields": [
      "name", "url", "telephone", "email", "address", "geo",
      "openingHoursSpecification", "servesCuisine", "priceRange",
      "acceptsReservations", "aggregateRating"
    ],
    "optional_fields": [
      "hasMenu", "menu", "image", "logo", "founder",
      "paymentAccepted", "currenciesAccepted"
    ],
    "field_mappings": {
      "servesCuisine": "restaurant.cuisine_types",
      "priceRange": "restaurant.food_price_range",
      "acceptsReservations": "restaurant.accepts_reservations",
      "hasMenu": {
        "@type": "Menu",
        "url": "restaurant.menu_url",
        "hasMenuSection": "restaurant.menu_sections"
      }
    }
  },

  "conditional_blocks": {
    "hasCredential": {
      "condition": "electrician.license OR root.license",
      "note": "Only emitted for licensed trades — restaurants skip this"
    }
  },

  "page_schemas": {
    "menu": {
      "additional_types": ["Menu"],
      "note": "Menu page gets Menu structured data with MenuSection items"
    },
    "reserve-order": {
      "additional_types": ["ReserveAction"],
      "note": "Reservation page gets potentialAction with ReserveAction"
    }
  }
}
```

Compare electrician:

```jsonc
// profiles/electrician/schema-template.json
{
  "business_type": "electrician",
  "primary_type": "LocalBusiness",
  "additional_types": [],

  "business_node": {
    "@type": "LocalBusiness",
    "required_fields": [
      "name", "url", "telephone", "email", "address", "geo",
      "openingHoursSpecification", "hasCredential", "aggregateRating",
      "areaServed"
    ],
    "optional_fields": ["founder", "image", "logo", "priceRange"],
    "field_mappings": {
      "hasCredential.credentialCategory": "electrician.license.category",
      "hasCredential.recognizedBy.name": "electrician.license.issuer"
    }
  },

  "conditional_blocks": {},

  "page_schemas": {
    "_matrix_page": {
      "additional_types": ["Service", "FAQPage"],
      "note": "Each service×city page gets Service + FAQPage nodes"
    }
  }
}
```

### 4.3 `keyword-research.json` — Patterns for research

```jsonc
// profiles/restaurant/keyword-research.json
{
  "business_type": "restaurant",

  "primary_patterns": [
    "{cuisine} restaurant {city}",
    "{cuisine} food near me",
    "best {cuisine} food {city}",
    "{cuisine} restaurant near me"
  ],

  "dish_patterns": [
    "{dish_name} near me",
    "best {dish_name} {city}",
    "where to get {dish_name} {city}"
  ],

  "intent_patterns": [
    "{cuisine} delivery {city}",
    "{cuisine} takeout near me",
    "{cuisine} restaurant open now",
    "restaurants with {dietary_option} options {city}",
    "{city} restaurant reservations"
  ],

  "modifiers": [
    "best", "authentic", "cheap", "near me", "delivery",
    "takeout", "dine in", "open now", "open late"
  ],

  "competitor_patterns": [
    "{cuisine} restaurant {city}",
    "Asian restaurant {city}",
    "best restaurants {neighborhood}"
  ]
}
```

### 4.4 `content-sections.json` — Section templates + CTA patterns + Token Bindings

This file serves two roles: (a) declaring content sections and CTA patterns per type, and (b) **declaring the `token_bindings` map** that tells the engine how to build the substitution dict from client config without any per-type code. This is the mechanism that replaces the current hardcoded `build_context()` (scaffold-core-30-page.py L185–271).

#### Token Bindings — How the Engine Resolves Template Tokens

The current engine's `build_context()` hand-assembles ~40 substitution keys with hardcoded type-specific paths (e.g., `sub["license_state"] = client["license"]["state"]`, `sub["city_ev_homes_phrase"] = city["ev_charger_homes_phrase"]`). This is where the electrician coupling actually lives.

The fix: each profile declares a `token_bindings` map — `{ token_name: config_path }` — and the engine builds the substitution dict by walking that map. **The engine has zero knowledge of what tokens exist; the profile defines them all.**

Resolution rules for `config_path` values:
- `"root.<field>"` → `client["<field>"]` (universal field at config root)
- `"root.address.<field>"` → `client["address"]["<field>"]` (nested universal)
- `"<business_type>.<field>"` → `client["<business_type>"]["<field>"]` (type-specific section)
- `"<business_type>.<a>.<b>"` → `client["<business_type>"]["<a>"]["<b>"]` (nested type-specific)
- `"service.<field>"` → `service_data["<field>"]` (from service data file, matrix mode only)
- `"city.<field>"` → `city_data["<field>"]` (from city data file, matrix mode only)
- `"computed.<name>"` → engine calls a named compute function (for derived values like `county_short`, `no_trip_charge_cities_phrase`). Compute functions are registered in the engine, but which ones a profile uses is the profile's choice.
- `"literal:<value>"` → static string (e.g., `"literal:kw-"` for CSS prefix)
- `"default:<path>:<fallback>"` → resolves `<path>`, falls back to `<fallback>` if missing

```jsonc
// profiles/restaurant/content-sections.json
{
  "business_type": "restaurant",

  "token_bindings": {
    // --- Universal tokens (every business type gets these) ---
    "client_slug":        "root.client_slug",
    "client_name":        "root.name",
    "client_alt_name":    "root.alternate_name",
    "owner_name":         "root.owner_name",
    "owner_first_name":   "root.owner_first_name",
    "owner_title":        "root.owner_title",
    "phone_display":      "root.phone_display",
    "phone_tel":          "root.phone_tel",
    "phone_e164":         "root.phone_e164",
    "email":              "root.email",
    "website_url":        "root.website_url",
    "website_url_no_slash": "root.website_url_no_slash",
    "contact_page_path":  "root.contact_page_path",
    "secondary_cta_label": "root.secondary_cta_label",
    "response_promise":   "root.final_cta_response_promise",
    "review_count":       "root.review_count",
    "review_count_phrase": "root.review_count_phrase",
    "review_pitch":       "root.review_pitch",
    "review_rating":      "root.review_rating",
    "brand_image_url":    "root.brand_image_url",
    "brand_logo_url":     "root.brand_logo_url",
    "primary_color":      "root.primary_color",
    "navy":               "root.navy",
    "accent_yellow":      "root.accent_yellow",
    "heading_color":      "root.heading_color",
    "hero_gradient_dark": "root.hero_gradient_dark",
    "hero_gradient_mid":  "root.hero_gradient_mid",
    "hero_gradient_bright": "root.hero_gradient_bright",
    "city_name":          "root.address.locality",
    "city_state":         "root.address.region",

    // --- Restaurant-specific tokens ---
    "primary_cuisine":    "restaurant.primary_cuisine",
    "cuisine_types":      "restaurant.cuisine_types",
    "food_price_range":   "restaurant.food_price_range",
    "menu_url":           "restaurant.menu_url",
    "reservation_url":    "restaurant.reservation_url",
    "order_url":          "restaurant.order_url",
    "ambiance":           "restaurant.ambiance",
    "parking":            "restaurant.parking",

    // --- Computed tokens ---
    "todays_closing_time": "computed.todays_closing_time",

    // --- CSS prefix (type-level, not client-specific) ---
    "css_prefix":         "literal:rst"
  },

  "sections": {
    "hero": {
      "heading_template": "Authentic {primary_cuisine} Cuisine in {city_name}",
      "subheading_template": "Family recipes, fresh ingredients, served daily at {client_name}",
      "cta_primary": { "label": "Reserve a Table", "action": "link", "target": "restaurant.reservation_url" },
      "cta_secondary": { "label": "Order Online", "action": "link", "target": "restaurant.order_url" }
    },
    "cuisine-overview": {
      "heading_template": "A Taste of {primary_cuisine} Tradition",
      "body": "content-block",
      "note": "Authored content about the cuisine tradition — not templated, written per client"
    },
    "featured-dishes": {
      "heading_template": "Signature Dishes",
      "layout": "card-grid",
      "data_source": "restaurant.menu_sections[*].items (flagged as featured)"
    },
    "menu-sections": {
      "heading_template": "Our Menu",
      "layout": "accordion-or-table",
      "data_source": "restaurant.menu_sections"
    },
    "reservation-block": {
      "heading_template": "Reserve a Table",
      "cta": { "label": "Make a Reservation", "action": "link", "target": "restaurant.reservation_url" }
    },
    "order-block": {
      "heading_template": "Order Online",
      "cta": { "label": "Order for Pickup or Delivery", "action": "link", "target": "restaurant.order_url" },
      "delivery_partners_display": true
    },
    "hours-table": {
      "data_source": "hours",
      "layout": "day-by-day-table"
    },
    "reviews": {
      "heading_template": "What Our Guests Say",
      "data_source": "aggregateRating + review snippets"
    },
    "location-cta": {
      "heading_template": "Visit Us",
      "includes": ["address", "map-embed", "hours-summary"]
    }
  },

  "cta_patterns": {
    "primary_verb": "Reserve",
    "secondary_verb": "Order",
    "phone_cta": "Call {phone_display}",
    "urgency_pattern": "Open until {todays_closing_time} today"
  }
}
```

Compare electrician (showing the full token_bindings that replace the current hardcoded `build_context()`):

```jsonc
// profiles/electrician/content-sections.json (excerpt — token_bindings + cta_patterns)
{
  "business_type": "electrician",

  "token_bindings": {
    // --- Universal tokens (same as restaurant) ---
    "client_slug":        "root.client_slug",
    "client_name":        "root.name",
    "client_alt_name":    "root.alternate_name",
    "owner_name":         "root.owner_name",
    "owner_first_name":   "root.owner_first_name",
    "owner_title":        "root.owner_title",
    "phone_display":      "root.phone_display",
    "phone_tel":          "root.phone_tel",
    "phone_e164":         "root.phone_e164",
    "email":              "root.email",
    "website_url":        "root.website_url",
    "website_url_no_slash": "root.website_url_no_slash",
    "contact_page_path":  "root.contact_page_path",
    "secondary_cta_label": "root.secondary_cta_label",
    "response_promise":   "root.final_cta_response_promise",
    "review_count":       "root.review_count",
    "review_count_phrase": "root.review_count_phrase",
    "review_pitch":       "root.review_pitch",
    "review_rating":      "root.review_rating",
    "brand_image_url":    "root.brand_image_url",
    "brand_logo_url":     "root.brand_logo_url",
    "primary_color":      "root.primary_color",
    "navy":               "root.navy",
    "accent_yellow":      "root.accent_yellow",
    "heading_color":      "root.heading_color",
    "hero_gradient_dark": "root.hero_gradient_dark",
    "hero_gradient_mid":  "root.hero_gradient_mid",
    "hero_gradient_bright": "root.hero_gradient_bright",

    // --- Electrician-specific tokens (these are what build_context L216–263 currently hardcodes) ---
    "license_state":      "electrician.license.state",
    "license_state_full": "electrician.license.state_full",
    "css_prefix":         "literal:evp",

    // --- City-derived tokens (matrix mode — resolved per city data file) ---
    "city_slug":          "city.slug",
    "city_name":          "city.name",
    "city_state":         "city.state",
    "city_name_with_state": "city.name_with_state",
    "city_county":        "city.county",
    "city_county_full":   "city.county_full",
    "city_distance_from_hq_phrase": "city.distance_from_hq_phrase",
    "geographic_anchor_paragraph":  "city.geographic_anchor_paragraph",
    "audience_descriptor":          "city.audience_descriptor",
    "other_areas_paragraph":        "city.other_areas_paragraph",
    "city_ev_homes_phrase":         "city.ev_charger_homes_phrase",
    "utility_coordination_phrase":  "default:city.utility_coordination_phrase:Dominion Energy coordination",
    "dispatch_time_phrase":         "default:city.dispatch_time_phrase:45-minute",
    "dispatch_time_short":          "default:city.dispatch_time_short:45-min",

    // --- Service-derived tokens (matrix mode — resolved per service data file) ---
    "service_slug":       "service.slug",
    "service_name":       "service.name",
    "service_tag":        "service.tag_short",
    "service_lowercase":  "service.lowercase_phrase",

    // --- Computed tokens (engine-registered functions, profile selects which to invoke) ---
    "county_short":                "computed.county_short",
    "city_tag":                    "computed.city_tag",
    "no_trip_charge_cities_phrase": "computed.no_trip_charge_cities_phrase",
    "page_slug":                   "computed.page_slug",
    "page_url":                    "computed.page_url"
  },

  "cta_patterns": {
    "primary_verb": "Call",
    "secondary_verb": "Request a Quote",
    "phone_cta": "Call {phone_display}",
    "urgency_pattern": "{dispatch_time_phrase} response"
  }
}
```

### 4.5 `config-schema.json` — Required/optional client fields for this type

```jsonc
// profiles/restaurant/config-schema.json
{
  "business_type": "restaurant",

  "required_type_fields": {
    "restaurant.cuisine_types": { "type": "array[string]", "min_items": 1 },
    "restaurant.primary_cuisine": { "type": "string" },
    "restaurant.food_price_range": { "type": "string", "enum": ["$", "$$", "$$$", "$$$$"] },
    "restaurant.accepts_reservations": { "type": "boolean" }
  },

  "optional_type_fields": {
    "restaurant.menu_url": { "type": "url" },
    "restaurant.menu_sections": { "type": "array[MenuSection]" },
    "restaurant.reservation_url": { "type": "url", "condition": "accepts_reservations == true" },
    "restaurant.order_url": { "type": "url" },
    "restaurant.delivery_partners": { "type": "array[string]" },
    "restaurant.dietary_options": { "type": "array[string]" },
    "restaurant.ambiance": { "type": "string" },
    "restaurant.seating_capacity": { "type": "integer" },
    "restaurant.alcohol_served": { "type": "boolean" },
    "restaurant.parking": { "type": "string" },
    "restaurant.accepts_online_orders": { "type": "boolean" }
  },

  "fields_not_used": {
    "license": "Restaurants typically don't have trade licenses in client config. Health permits are handled differently (not a schema.org credential).",
    "services": "No service × city matrix for restaurants. Page model uses fixed page list.",
    "utility_providers": "Electrician-only concept."
  }
}
```

---

## 5. Engine Changes (scaffold-page.py)

### 5.1 Profile Loader

```python
def load_profile(business_type: str) -> dict:
    """Load all profile files for a business type."""
    profile_dir = SCRIPT_DIR / "profiles" / business_type
    if not profile_dir.is_dir():
        raise ValueError(f"No profile found for business_type={business_type!r}. "
                         f"Available: {[d.name for d in (SCRIPT_DIR / 'profiles').iterdir() if d.is_dir()]}")
    return {
        "page_model": load_json(profile_dir / "page-model.json"),
        "schema_template": load_json(profile_dir / "schema-template.json"),
        "keyword_research": load_json(profile_dir / "keyword-research.json"),
        "content_sections": load_json(profile_dir / "content-sections.json"),
        "config_schema": load_json(profile_dir / "config-schema.json"),
    }
```

### 5.2 Token Resolution — `build_context()` Walks the Profile's `token_bindings`

This is the core mechanism that makes the engine type-agnostic. The current `build_context()` (L185–271) hardcodes ~40 type-specific key assignments. The new version has **zero per-type knowledge** — it walks the profile's `token_bindings` map and resolves each path against the available data sources.

```python
# --- Registered compute functions (engine-level, type-agnostic) ---
COMPUTE_FUNCTIONS = {
    "county_short":                 lambda ctx: ctx["_city"]["county"].split(",")[0],
    "city_tag":                     lambda ctx: ctx["_resolved"]["city_name"].lower().replace(" ", "-"),
    "no_trip_charge_cities_phrase":  lambda ctx: _format_city_list(ctx["_city"]["no_trip_charge_cities"]),
    "page_slug":                    lambda ctx: ctx["_page_slug"],
    "page_url":                     lambda ctx: f"{ctx['_resolved']['website_url']}{ctx['_page_slug']}/",
    "todays_closing_time":          lambda ctx: _todays_closing_time(ctx["_client"]["hours"]),
    # _todays_closing_time: finds today's day-of-week in the hours array,
    # returns the "closes" value (e.g. "18:00" → "6 PM"), or "closed" if
    # today has no entry. Universal — works for any business with hours[].
}

def resolve_token(path: str, client: dict, service: dict | None,
                  city: dict | None, computed_ctx: dict) -> Any:
    """Resolve a single token_bindings path to a value.

    Path prefixes:
      root.<field>             → client[field]
      root.<a>.<b>             → client[a][b]
      <business_type>.<field>  → client[business_type][field]
      service.<field>          → service[field]  (matrix mode only)
      city.<field>             → city[field]     (matrix mode only)
      computed.<name>          → COMPUTE_FUNCTIONS[name](ctx)
      literal:<value>          → static string
      default:<path>:<fallback> → resolve(path), fallback if missing
    """
    if path.startswith("literal:"):
        return path[len("literal:"):]

    if path.startswith("default:"):
        _, inner_path, fallback = path.split(":", 2)
        try:
            return resolve_token(inner_path, client, service, city, computed_ctx)
        except (KeyError, TypeError):
            return fallback

    if path.startswith("computed."):
        fn_name = path[len("computed."):]
        return COMPUTE_FUNCTIONS[fn_name](computed_ctx)

    parts = path.split(".")
    prefix = parts[0]
    remainder = parts[1:]

    if prefix == "root":
        obj = client
    elif prefix == "service":
        if service is None:
            raise ValueError(f"Token {path!r} requires service data (matrix mode)")
        obj = service
    elif prefix == "city":
        if city is None:
            raise ValueError(f"Token {path!r} requires city data (matrix mode)")
        obj = city
    else:
        # Type-specific section: prefix is the business_type key
        obj = client[prefix]

    for key in remainder:
        obj = obj[key]
    return obj


def build_context(client: dict, profile: dict,
                  service: dict | None = None,
                  city: dict | None = None,
                  page_slug: str = "") -> dict:
    """Build the substitution dict by walking the profile's token_bindings.

    NO per-type conditionals. Every token the templates reference is
    declared in the profile; the engine just resolves paths.
    """
    bindings = profile["content_sections"]["token_bindings"]
    computed_ctx = {"_client": client, "_city": city or {}, "_resolved": {}, "_page_slug": page_slug}

    sub = {}
    for token_name, config_path in bindings.items():
        sub[token_name] = resolve_token(config_path, client, service, city, computed_ctx)
        computed_ctx["_resolved"][token_name] = sub[token_name]

    # Timestamps (universal, not in bindings)
    sub["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    sub["today"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # ── PASS 2: Render intermediate template strings ──
    # The current engine (L282–319) renders ~27 *_template fields from service
    # data files via .format_map(sub), producing rendered content tokens
    # (hero_eyebrow, hero_subheading, pricing_intro, aioseo_page_title, etc.).
    # These are themselves templates referencing tokens resolved in Pass 1.
    #
    # The profile declares which fields to render in a "template_renders" list.
    # Each entry maps: output_token → source_path (a template string in the
    # data) or a literal non-template value. The engine renders each template
    # string with .format_map(sub) and feeds the result back into sub, so
    # later templates can reference earlier-rendered tokens.
    #
    # Ordering: entries are processed in list order. Since rendered tokens
    # feed back into sub, a template can reference tokens from Pass 1 AND
    # any earlier Pass 2 renders. (The current engine relies on this —
    # e.g., hero_subheading_template uses {license_state} from Pass 1.)
    # No iterate-to-fixpoint needed; single sequential pass suffices because
    # the current code is strictly sequential (L282–319).

    for render_spec in profile["content_sections"].get("template_renders", []):
        token_name = render_spec["token"]
        source_path = render_spec["source"]
        is_template = render_spec.get("is_template", True)

        raw_value = resolve_token(source_path, client, service, city, computed_ctx)

        if is_template and isinstance(raw_value, str):
            sub[token_name] = raw_value.format_map(sub)
        elif is_template and isinstance(raw_value, list):
            # Handles lists of template strings (e.g., aioseo_additional_keywords_template)
            sub[token_name] = [item.format_map(sub) if isinstance(item, str) else item for item in raw_value]
        else:
            sub[token_name] = raw_value

    # Stash raw data for section renderers that need structured access
    sub["_client"] = client
    sub["_service"] = service
    sub["_city"] = city
    return sub
```

**Pass 2 example — electrician profile declares all ~27 template renders:**

```jsonc
// profiles/electrician/content-sections.json → template_renders (excerpt)
"template_renders": [
  { "token": "hero_eyebrow",    "source": "service.hero_eyebrow_template" },
  { "token": "hero_heading",    "source": "service.hero_heading",           "is_template": false },
  { "token": "hero_subheading", "source": "service.hero_subheading_template" },
  { "token": "hero_image_alt",  "source": "service.hero_image_alt_template" },
  { "token": "hero_image_url",  "source": "service.hero_image_url_template" },
  { "token": "what_it_means_heading", "source": "service.what_it_means_heading", "is_template": false },
  { "token": "quick_ref_heading",     "source": "service.quick_ref_heading_template" },
  { "token": "quick_ref_intro",       "source": "service.quick_ref_intro",         "is_template": false },
  { "token": "quick_ref_footer_html", "source": "service.quick_ref_footer_html_template" },
  { "token": "why_city_heading",      "source": "service.why_city_heading_template" },
  { "token": "why_city_closing_note", "source": "service.why_city_closing_note_template" },
  { "token": "problems_heading",      "source": "service.problems_heading_template" },
  { "token": "problems_intro",        "source": "service.problems_intro_template" },
  { "token": "process_heading",       "source": "service.process_heading_template" },
  { "token": "process_intro",         "source": "service.process_intro_template" },
  { "token": "pricing_heading",       "source": "service.pricing_heading",          "is_template": false },
  { "token": "pricing_intro",         "source": "service.pricing_intro_template" },
  { "token": "pricing_note",          "source": "service.pricing_note_template" },
  { "token": "pricing_closing_note",  "source": "service.pricing_closing_note",     "is_template": false },
  { "token": "about_heading",         "source": "service.about_heading_template" },
  { "token": "about_portrait_url",    "source": "service.about_portrait_url_template" },
  { "token": "about_portrait_alt",    "source": "service.about_portrait_alt_template" },
  { "token": "neighborhoods_heading", "source": "service.neighborhoods_heading_template" },
  { "token": "neighborhoods_intro",   "source": "service.neighborhoods_intro_template" },
  { "token": "related_heading",       "source": "service.related_heading_template" },
  { "token": "related_intro",         "source": "service.related_intro_template" },
  { "token": "final_cta_heading",     "source": "service.final_cta_heading",        "is_template": false },
  { "token": "final_cta_paragraph",   "source": "service.final_cta_paragraph_template" },
  { "token": "faq_heading",           "source": "service.faq_heading",              "is_template": false },
  { "token": "aioseo_page_title",     "source": "service.aioseo_page_title_template" },
  { "token": "aioseo_meta_description", "source": "service.aioseo_meta_description_template" },
  { "token": "wordpress_page_title",  "source": "service.wordpress_page_title_template" },
  { "token": "focus_keyword",         "source": "service.aioseo_focus_keyword_template" },
  { "token": "aioseo_additional_keywords", "source": "service.aioseo_additional_keywords_template" }
]
```

**Pass 2 example — restaurant profile declares its own (shorter) template renders:**

```jsonc
// profiles/restaurant/content-sections.json → template_renders (excerpt)
"template_renders": [
  { "token": "hero_heading",    "source": "literal:Authentic {primary_cuisine} Cuisine in {city_name}" },
  { "token": "hero_subheading", "source": "literal:Family recipes, fresh ingredients, served daily at {client_name}" },
  { "token": "page_title",      "source": "literal:{client_name} — {primary_cuisine} Restaurant in {city_name}" }
]
```

Restaurant has far fewer template renders because its content sections are simpler (no service-level template indirection). Both profiles use the same engine mechanism.

**Authoritative source for rendered tokens:** `template_renders` is the single source that the engine executes. The `sections.*.heading_template` / `subheading_template` fields in the `sections` block are **declarative metadata** for documentation and tooling (e.g., a future section-layout renderer that reads section definitions). They are NOT consumed by `build_context()`. If both exist for the same content (as with restaurant's hero heading), the `template_renders` entry is what produces the token in `sub`; the `sections` entry describes intent. Implementers must not double-render.

This design means:
- Adding a new token for restaurant = add one line to `token_bindings`. Adding a new rendered template = add one entry to `template_renders`. Zero engine changes in both cases.
- The electrician profile's `token_bindings` reproduces every key that the current `build_context()` L185–271 hardcodes, and `template_renders` reproduces every `.format_map()` call at L282–319 — verified by the regression test (§7.5).
- `service.*` and `city.*` paths are only valid in matrix mode; a fixed-list profile that references them will get a clear error.

### 5.3 JSON-LD Builder — Profile-Driven

The current `build_jsonld()` hardcodes `@type: LocalBusiness` and always emits `hasCredential`. The new version:

1. Reads `@type` from `profile["schema_template"]["primary_type"]`
2. Reads required/optional fields from `profile["schema_template"]["business_node"]`
3. Uses `field_mappings` to resolve values from client config (via `resolve_token()` — same resolver as content tokens)
4. Emits `conditional_blocks` only when their condition path evaluates truthy (e.g., `hasCredential` only when `electrician.license` exists)
5. Adds page-specific schema nodes from `profile["schema_template"]["page_schemas"]`
6. **Always uses `JSON_HEX_TAG`** when serializing (CR-055 compliance)

### 5.4 Page Generation — Two Modes + Data Resolution

```python
def generate_pages(client: dict, profile: dict) -> list[dict]:
    """Return the list of pages to build, resolving per the page model's generation mode."""
    page_model = profile["page_model"]

    if page_model.get("page_generation") == "matrix":
        # MATRIX MODE (electrician): expand axis_a × axis_b
        # axis_a / axis_b name the data directories under data/
        # e.g., axis_a="services" → data/services/*.json, axis_b="cities" → data/cities/*.json
        # The axis values resolve to file lists, NOT client config keys —
        # the client config's type-specific section (e.g., electrician.services)
        # provides the FILTER (which slugs to include), while the full data
        # lives in the per-slug JSON files.
        matrix = page_model["matrix"]
        return expand_matrix(client, matrix)
    else:
        # FIXED-LIST MODE (restaurant): iterate pages[] directly.
        # Per-page data comes from the client config's type-specific section
        # (e.g., client["restaurant"]["menu_sections"] for the menu page).
        # The content-sections profile declares which data_source each section reads.
        # No separate data/pages/*.json files needed — the client config IS the data source.
        return page_model["pages"]


def expand_matrix(client: dict, matrix: dict) -> list[dict]:
    """Expand a service × city matrix into individual page dicts.

    Resolution:
      axis_a="services" → list service slugs from client[business_type]["services"]
                        → load data/services/<slug>.json for each
      axis_b="cities"   → list city slugs from client[business_type]["service_area_cities"]
                        → load data/cities/<slug>.json for each
    """
    btype = client["business_type"]
    type_section = client[btype]

    service_slugs = type_section["services"]         # e.g., ["troubleshooting", "panel-upgrade", ...]
    city_slugs = type_section["service_area_cities"]  # e.g., ["vienna-va", "fairfax-va", ...]

    pages = []
    for svc_slug in service_slugs:
        service_data = load_service(svc_slug, client["client_slug"])
        for city_slug in city_slugs:
            city_data = load_city(city_slug, client["client_slug"])
            pages.append({
                "slug": matrix["slug_template"].format(service_slug=svc_slug, city_slug=city_slug),
                "title_template": matrix["title_template"],
                "service_data": service_data,
                "city_data": city_data,
            })
    return pages
```

**This resolves Open Q #3:** Fixed-list pages (restaurant) get their per-page data from the client config's type-specific section (e.g., `client["restaurant"]["menu_sections"]`), routed via the `data_source` field in the content-sections profile. No parallel `data/pages/*.json` concept needed. Matrix pages (electrician) continue loading from `data/services/` and `data/cities/` — the service/city slugs to expand come from `client["electrician"]["services"]` and `client["electrician"]["service_area_cities"]`.

### 5.5 What Stays in the Engine (Universal)

These are business-type-agnostic and stay in the engine:

| Component | Why Universal |
|---|---|
| Client config loader | Reads any JSON, extracts `business_type` |
| Profile loader | Directory-based dispatch, no type knowledge |
| Token resolver (`resolve_token`) | Walks paths declared by profile — no per-type code |
| Context builder (`build_context`) | Iterates `token_bindings` from profile — no hardcoded keys |
| JSON-LD graph builder | Reads schema template, builds nodes — type comes from profile |
| HTML template renderer | `format_map()` on substitution dict — tokens come from profile |
| Compute functions | Registered set (county_short, city_tag, etc.) — profile selects which to use |
| `keelworks-jsonld-head.php` | Already fully agnostic |
| Map embed generator | Takes lat/lng, returns iframe — works for any address |
| AIOSEO metadata | Template-driven, tokens from profile |
| File writer (draft + HTML) | Just writes to disk |

### 5.6 What Moves OUT of the Engine (Into Profiles)

| Currently Hardcoded | Moves To |
|---|---|
| `@type: LocalBusiness` | `profiles/<type>/schema-template.json → primary_type` |
| `hasCredential` always emitted | `profiles/electrician/schema-template.json → required_fields` (not in restaurant) |
| `ev_charger_homes_phrase` in city data | `profiles/electrician/` — electrician-only city fields |
| `utility_coordination_phrase` fallback "Dominion Energy" | `profiles/electrician/config-schema.json` — electrician default |
| `dispatch_time_phrase` fallback "45-minute" | `profiles/electrician/config-schema.json` — electrician default |
| Lightning bolt SVG in template | `profiles/electrician/assets/icon.svg` — icon is per-type |
| `evp-` CSS class prefix | Template becomes `{css_prefix}-` — set by profile |
| "homeowners" in section copy | Content section template — `profiles/<type>/content-sections.json` |
| Service × city page generation | `profiles/electrician/page-model.json → page_generation: "matrix"` |
| "Call now" CTA pattern | `profiles/electrician/content-sections.json → cta_patterns` |
| Service-specific data files | `data/services/` stays — but restaurant doesn't use it (no service × city) |

---

## 6. Directory Layout (After BTF-1)

```
repos/ai-agency-core/scripts/
├── scaffold-page.py                    # Renamed from scaffold-core-30-page.py
├── profiles/
│   ├── electrician/
│   │   ├── page-model.json
│   │   ├── schema-template.json
│   │   ├── keyword-research.json
│   │   ├── content-sections.json
│   │   ├── config-schema.json
│   │   └── assets/
│   │       └── icon.svg               # Lightning bolt
│   └── restaurant/
│       ├── page-model.json
│       ├── schema-template.json
│       ├── keyword-research.json
│       ├── content-sections.json
│       ├── config-schema.json
│       └── assets/
│           └── icon.svg               # Fork & knife or similar
├── templates/
│   ├── base-page.html.tmpl            # Universal HTML skeleton
│   ├── sections/                       # Universal section renderers
│   │   ├── hero.html.tmpl
│   │   ├── reviews.html.tmpl
│   │   ├── location.html.tmpl
│   │   ├── about.html.tmpl
│   │   └── ...
│   └── core-30-page.html.tmpl         # Legacy — kept for backward compat during migration
├── data/
│   ├── client-ev-electric-services.json   # Updated with business_type + electrician section
│   ├── client-s-and-h-contracting.json    # Updated
│   ├── client-asian-delight.json          # NEW
│   ├── services/                           # Electrician services (unchanged)
│   └── cities/                             # Electrician cities (unchanged)
└── generate-maps-iframe.py                 # Unchanged — already universal
```

---

## 7. Safety Constraints

1. **JSON_HEX_TAG on all JSON injected into `<script>`** — CR-055 class. The engine's JSON-LD serializer MUST use `json.dumps(..., ensure_ascii=False)` with manual `</` escaping or `JSON_HEX_TAG` equivalent. The existing `keelworks-jsonld-head.php` already does this; the Python scaffolder must match.

2. **Placeholder content clearly marked** — any researched/placeholder content in Asian Delight output files MUST include `<!-- PLACEHOLDER: ... -->` HTML comments and/or a frontmatter `content_status: placeholder` field. Never present placeholder as real client data.

3. **ACCESS-GATED steps flagged, not executed** — live-publish (WP/domain), real GBP changes, and real client-facing content are flagged in output with `<!-- ACCESS-GATED: requires [specific access] -->`. The scaffolder produces local files only.

4. **No Asian Delight or restaurant hardcoding in the engine** — verified by: the engine file contains zero occurrences of "restaurant", "asian", "delight", "burmese", "cuisine", "menu" as string literals. All such references live in profiles/ or data/.

5. **Electrician regression** — after engine changes, `scaffold-page.py --client ev-electric-services --service troubleshooting --city vienna-va` MUST produce identical JSON-LD and functionally equivalent HTML to the current `scaffold-core-30-page.py` output.

### 7.5 Electrician JSON-LD Regression — Full Node Inventory

The current `build_jsonld()` (L646–737) emits the following `@graph` structure. The extracted electrician profile MUST reproduce every node and nested type declaratively. This is the regression bar.

```
@graph[0]: LocalBusiness
  ├── @type: "LocalBusiness"
  ├── @id: "{website_url}#business"
  ├── name, alternateName, description, url, telephone, email, image, logo, priceRange
  ├── address → PostalAddress (streetAddress, addressLocality, addressRegion, postalCode, addressCountry)
  ├── geo → GeoCoordinates (latitude, longitude)
  ├── areaServed → [City] (each with name + containedInPlace → AdministrativeArea)
  │   └── Falls back to brand_areas_served if no city.area_served_schema
  ├── openingHoursSpecification → [OpeningHoursSpecification] (dayOfWeek, opens, closes)
  ├── hasCredential → EducationalOccupationalCredential
  │   ├── credentialCategory (from electrician.license.category)
  │   └── recognizedBy → Organization (name from electrician.license.issuer)
  ├── founder → Person
  │   ├── name (owner_name), jobTitle (owner_title)
  │   └── worksFor → @id reference back to #business
  └── aggregateRating → AggregateRating
      └── ratingValue, reviewCount, bestRating="5", worstRating="1"

@graph[1]: Service
  ├── @type: "Service"
  ├── @id: "{page_url}#service"
  ├── name (name_with_city template), description (schema_service_description_template)
  ├── serviceType (service_type_phrase)
  ├── provider → @id reference to #business
  ├── areaServed → City (name + containedInPlace → AdministrativeArea with county_full)
  ├── audience → Audience (audienceType from city.audience_descriptor)
  ├── offers → Offer
  │   ├── priceCurrency: "USD", price, availability: InStock, url
  │   └── priceSpecification → PriceSpecification (price, priceCurrency, description)
  └── termsOfService (schema_service_terms)

@graph[2]: FAQPage
  ├── @type: "FAQPage"
  ├── @id: "{page_url}#faq"
  └── mainEntity → [Question]
      └── each: name (question), acceptedAnswer → Answer (text)
```

The electrician `schema-template.json` must declare all of these nodes, their nesting, and their field_mappings so the profile-driven `build_jsonld()` can reproduce this graph exactly. Any omitted node (e.g., missing `EducationalOccupationalCredential`, missing `PriceSpecification` inside `Offer`) is a regression failure.

---

## 8. What This Spec Does NOT Cover (Future Waves)

| Item | Why Deferred |
|---|---|
| Refactoring `client-seo-onboarding` SKILL.md | Wave 2 — the orchestrator skill is large; parameterizing it is a separate effort |
| Template HTML refactor (split `core-30-page.html.tmpl` into composable sections) | Wave 2 — needed for restaurant pages but the spec above shows the target structure |
| WordPress-vs-static routing | Wave 2 — restaurant may not be WordPress |
| Imagery profile (real food photography vs electrician stock) | Wave 2 — access-gated on real client photos |
| Third business type (plumber, HVAC, etc.) | Wave 3 — proves the pattern generalizes beyond 2 types |
| Full skill/script gap audit automation (`onboarding-gap-audit` skill) | Wave 3 |

---

## 9. Acceptance Criteria for Wave 1

- [x] Architecture spec reviewed and approved by operator (THIS document) — 4-round review PASS
- [x] `profiles/restaurant/` — all 5 profile files exist and are internally consistent
- [x] `profiles/electrician/` — all 5 profile files exist, extracted from current hardcoded values
- [x] `client-asian-delight.json` — valid config with `business_type: "restaurant"` + restaurant section
- [x] Unified `scaffold-page.py` — single engine entry point for both matrix + fixed-list modes
- [x] JSON-LD builder is fully profile-driven for BOTH types (zero type conditionals)
- [x] Restaurant/fixed-list path is fully profile-driven (token resolution, sections, page generation)
- [x] Electrician HTML is rendered via legacy delegation (same-code, byte-identical, zero-risk) — electrician HTML generalization is a tracked Wave 2 deliverable
- [x] Asian Delight pages scaffolded locally with placeholder content, correct `Restaurant` JSON-LD, page-gated conditional blocks
- [x] Electrician regression PASS: byte-identical HTML + value-identical JSON-LD on 2 pairs (mclean-va + falls-church-va)
- [x] Pipeline wired: `bulk-scaffold-pages.py` + 7 referencing scripts point to `scaffold-page.py`
- [x] Gap audit document classifying all 42 skills/scripts as universal / type-specific / electrician-only
- [x] Wave plan for reaching "any business type" — Waves 2 (electrician HTML generalization), 3 (full pipeline), 4 (type #3 validation)

---

## 10. Open Questions for Operator

1. **Engine rename:** Rename `scaffold-core-30-page.py` → `scaffold-page.py`? "Core 30" is electrician-specific terminology. The rename would be cleaner but touches import paths in other scripts.

2. **Template strategy:** Should Wave 1 build new HTML templates for restaurant pages, or reuse the existing `core-30-page.html.tmpl` structure with restaurant tokens? Reusing is faster but the existing template has electrician-specific structure (problem cards, process steps, pricing section) that don't map to restaurant pages.

3. **Service data files:** Restaurants don't have `data/services/<slug>.json` files. The restaurant profile's page model uses a fixed page list. Should the engine completely skip service data loading when `page_generation != "matrix"`, or should we introduce a parallel concept (e.g., `data/pages/<slug>.json` for fixed-list page data)?

4. **Profile validation:** Should `scaffold-page.py` validate client config against `profiles/<type>/config-schema.json` at load time and fail fast on missing required fields? (Recommended: yes, with `--skip-validation` escape hatch for WIP configs.)
