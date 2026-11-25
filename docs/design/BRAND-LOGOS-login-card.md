# Provider logos on the sign-in card

**The four providers' logos are used without their written permission, by the owner's decision of 2026-11-26 ("B+直接找他們的logo 放").** All four brand guidelines that could be read ask for permission first (quotes below); the owner decided with that known. Review and later maintainers: treat this as a deliberate, recorded decision, and remove a logo at once if its owner asks.

How the logos are used, to keep the risk low:
- Each file is the provider's own file, byte for byte (embedded as base64 in `apps/web/dist/ws.js`, `RELOG_LOGO`). Nothing recoloured, reshaped or with effects; no third-party icon sites; nothing loaded from the network.
- Each sits in a small neutral square (`.rlclogo`, dark #0c0d10; Grok's file is drawn in `currentColor`, which an `<img>` paints black, so Grok's square is white, `.rlclogo-light`), never tinted with the brand colour, and always next to the provider's name in text. The logo only says which service you are signing in to; it is not our product's mark.
- `apps/web/test/ws-static.test.cjs` (`relogcard_logo_official_bytes`) checks each embedded file against the sha256 below.

| Provider | File used | Where it came from (fetched 2026-11-26) | sha256 of the file used |
|---|---|---|---|
| Codex (OpenAI) | `OAI_OpenAI-Blossom_White.svg` (the Blossom) | https://openai.com/brand/ → https://cdn.openai.com/brand/openai-logos.zip, `OpenAI-logos/SVGs/` | 01d158767c4eec0e47bd617e67759c33da0accd1438be1a8d29dfdb99ce87285 |
| GitHub Copilot | `GitHub_Invertocat_White.svg` (GitHub's mark; Copilot has had no standalone logo since 2025, so no Copilot icon is drawn) | https://brand.github.com/foundations/logo → https://brand.github.com/GitHub_Logos.zip, `GitHub Logos/SVG/` | ccd84c89b1056345608fc3489357f8acc7397e49a3cdc2d418b6c8016911d47b |
| Claude (Anthropic) | `Claude Spark - Clay.svg` | https://www.anthropic.com/press-kit (downloads the media zip), `Anthropic logos/Claude logos/3 Claude Spark/SVG/` | 6d53db4be375e899c937c26cf16684a80d6e869b1928d72b37748bef2560e219 |
| Grok (xAI) | the header mark of https://docs.x.ai/ (inline SVG, one path), byte for byte | x.ai, grok.com and the brand guidelines page refuse automated reads (HTTP 403); docs.x.ai is xAI's own site. Unchanged: it keeps `fill:currentColor`, which shows black in an `<img>`, hence the white square | 349cb7362ffe9ec76489ace9732255d6fa80f645cb7e172d69d9c126963a0bd2 |

Archives as downloaded (sha256): openai-logos.zip c54e85ab5884228f89f0230dd8effa8d588cad78166fe954135f4afa553222db · GitHub_Logos.zip e2a67d6cc51d990a52c46c1cf6bcab688db4830982174bca50e0be7a5c2f3194 · Anthropic press kit zip c68ac92df86c825f95177e24016fcc9a8863a3fd4ca344fe6f0700b2c1e07151.

## What each guideline says (quoted, read 2026-11-26)

| Provider | Guideline | Permission | Changes |
|---|---|---|---|
| OpenAI | https://openai.com/brand/ | lists "Use the logo without permission" as a don't; "Use the logo only when it directly relates to OpenAI services" | "Use the logo exactly as provided"; "DON'T add any colors to the Blossom" |
| GitHub | https://brand.github.com/foundations/logo | "Do not use GitHub trademarks, logos, or artwork without GitHub's prior written permission" (the page also lists "Show integration: Use a permitted GitHub logo to inform others that your project integrates with GitHub") | "should only appear in white, black, or in few cases grey or green"; no changes to colour or dimensions |
| Anthropic | https://www.anthropic.com/legal/trademark-guidelines (effective 2024-08-01) | "only in materials we approve beforehand" | "No alterations of our trademarks (changes to color, font, proportion, or otherwise)" |
| xAI | https://x.ai/legal/brand-guidelines | could not be read (403) | could not be read |

Contacts if permission is sought later: OpenAI partnercomms@openai.com · GitHub https://github.com/contact · Anthropic marketing@anthropic.com · xAI legal@x.ai (from search results; not read on the page).
