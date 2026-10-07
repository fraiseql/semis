---
title: Shipped Provider Libraries
description: The i18n and organization libraries, each provider they hold, and the rule that matches it to columns by name, type and length
---

semis ships two provider libraries. Each is enabled by its name under `providers:` in
[semis.yaml](/reference/semis-yaml/#providers), and only then. Once enabled, a library
draws every column one of its rules matches: the column's name is one of the rule's
names, its type is text (`text`, `varchar`, `char` or `citext`), and it declares at least
the rule's minimum length, or no length at all. A rule says how a column is filled, not
whether: a nullable column is drawn only when the scenario names it, under `fill:` or by
a provider. A scenario also names any of their providers for one column, as
`<library>.<provider>`.

A rule never puts a value longer than the column holds: a three-letter code is not
matched to a `char(2)`, and text a rule draws is cut to the column's declared length.

## `i18n`

Countries, languages, locales, currencies and time zones, as their standards spell them.

| Provider | Matches columns named | Types | Min length | For example |
|---|---|---|---|---|
| `i18n.country_code` | `country_code`, `iso_country_code`, `country_iso_code`, `iso_code` | text | 2 | `GW`: ISO 3166-1 alpha-2 |
| `i18n.country_code_alpha3` | `country_code_alpha3`, `iso3` | text | 3 | `UKR`: ISO 3166-1 alpha-3 |
| `i18n.country_name` | `country_name` | text | any | `Mayotte` |
| `i18n.language_code` | `language_code`, `iso_language_code`, `lang` | text | 2 | `et`: ISO 639-1 |
| `i18n.language_name` | `language_name` | text | any | `Limburgan` |
| `i18n.locale` | `locale`, `locale_code` | text | any | `tg_TJ`: a POSIX locale name |
| `i18n.currency_code` | `currency_code`, `iso_currency_code`, `currency` | text | 3 | `YER`: ISO 4217 |
| `i18n.currency_name` | `currency_name` | text | any | `United States dollar` |
| `i18n.currency_symbol` | `currency_symbol` | text | any | `$` |
| `i18n.timezone` | `timezone`, `time_zone`, `tz` | text | any | `Asia/Samarkand`: an IANA zone, by its canonical name |

A time zone is always the canonical IANA name, never a legacy alias such as
`Asia/Calcutta`, which a PostgreSQL built without the legacy zone links refuses.

## `organization`

Companies and the people in them: names, French registry numbers, titles and contacts.

| Provider | Matches columns named | Types | Min length | For example |
|---|---|---|---|---|
| `organization.company` | `company`, `company_name`, `legal_name`, `organization_name` | text | any | `Watkins and Sons` |
| `organization.siren` | `siren` | text | 9 | `597919075`: nine digits, the last a Luhn check digit |
| `organization.siret` | `siret`, `legal_identifier` | text | 14 | `48337887362325`: a SIREN, four digits and a Luhn check digit |
| `organization.vat_number` | `vat_number`, `vat_identifier`, `vat_id` | text | 13 | `FR43860129048`: `FR`, the key its SIREN determines, the SIREN |
| `organization.job_title` | `job_title` | text | any | `Airline pilot` |
| `organization.department` | `department`, `department_name` | text | any | `Marketing` |
| `organization.email` | `email`, `email_address`, `company_email` | text | any | `ylarson@french.org` |
| `organization.website` | `website`, `web_site` | text | any | `https://www.davis-taylor.com/` |
| `organization.phone` | `phone`, `phone_number`, `office_phone`, `mobile_phone`, `fax` | text | 13 | `+34646869589`: E.164, at most thirteen characters |

SIREN, SIRET and VAT numbers pass their checks: the Luhn test, and the VAT key computed
from the SIREN.

## Where a library's rules come in

A column a scenario overrides, or names a provider for, is drawn as the scenario says. An
enum column draws one of its labels. Then the enabled libraries' rules apply, in the order
`semis.yaml` lists the libraries, and only then semis' built-in providers. See
[Writing scenarios](/guides/scenarios/#providers).

## Next steps

- [Writing a provider library](/guides/provider-libraries/): a library of a project's own
- [Writing scenarios](/guides/scenarios/#providers): naming a provider for one column
- [semis.yaml](/reference/semis-yaml/#providers): enabling a library
