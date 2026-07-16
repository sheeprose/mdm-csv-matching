# Schema RAG Knowledge

Use this content as Dify knowledge chunks for schema analysis and entity matching.

## Product Schema

Entity type: `product`

Typical columns:

- `id`: row identifier. Do not use for entity similarity.
- `title`, `name`, `product_name`: primary product name. High weight.
- `manufacturer`, `brand`, `vendor`, `maker`: brand or maker. Medium-high weight.
- `price`, `list_price`, `amount`: auxiliary numeric field. Low-medium weight.

Recommended weights:

```json
{"title": 0.45, "manufacturer": 0.35, "price": 0.20}
```

Reject product vs publication schemas. Example: `id,title,manufacturer,price` is not related to `id,title,authors,venue,year`.

## Publication Schema

Entity type: `publication`

Typical columns:

- `id`: row identifier. Do not use for entity similarity.
- `title`: paper title. Primary field and highest weight.
- `authors`: author list. Medium-high weight.
- `venue`: conference, journal, or venue. Medium weight.
- `year`: publication year. Auxiliary field.

Recommended weights:

```json
{"title": 0.45, "authors": 0.30, "venue": 0.15, "year": 0.10}
```

DBLP-Scholar and DBLP-ACM are compatible publication schemas when both contain publication fields.

## Restaurant Schema

Entity type: `restaurant`

Typical columns:

- `id`: row identifier. Do not use for entity similarity.
- `name`: restaurant name. Primary field.
- `addr`, `address`: street address. High weight.
- `city`: city or region. Medium weight.
- `phone`: telephone number. Strong evidence when equal.
- `type`: cuisine or restaurant type. Auxiliary field.
- `class`: dataset-internal label. Do not use for matching.

Recommended weights:

```json
{"name": 0.35, "addr": 0.25, "city": 0.15, "phone": 0.15, "type": 0.10}
```

Fodors-Zagats table pairs are compatible restaurant schemas.

## Rejection Rules

Return `related=false` when:

- The entity types are different, such as product vs publication, restaurant vs publication, or restaurant vs product.
- Only identifier columns can be mapped.
- There is no primary name/title/name field shared by both tables.
- Field names overlap but field semantics differ. A shared `title` column is not enough if the surrounding schema shows different domains.

## Column Role Rules

- `identifier`: `id`, `row_id`, `uuid`, `guid`, internal `class`. Weight must be `0`.
- `primary_name`: `title`, `name`, `product_name`. Usually weight `0.35` to `0.55`.
- `creator_or_brand`: `authors`, `manufacturer`, `brand`, `vendor`. Usually weight `0.20` to `0.35`.
- `location_or_context`: `addr`, `address`, `city`, `venue`. Usually weight `0.10` to `0.30`.
- `numeric_auxiliary`: `year`, `price`. Usually weight `0.05` to `0.15`.
- `phone`: exact phone equality is strong evidence. Usually weight `0.10` to `0.20`.
