# Find a retained session by its ID or title

Accepted2026-11-10 after independent L1 review and a targeted correction to the API boundary. Users can already copy a session ID from details, but the Sessions list only accepts dimension filters. Add a labeled literal, case-insensitive ID/title search over the complete loaded list before grouping or pagination. Clearly retain the current date/dimension scope; no conversation text or child-ID search is promised.

Reuse the existing list and rendering rather than adding backend query/cache contracts or changing the global page/help search. Browser Find misses hidden rows; manual detail URLs require additional machine/agent information. Store q only in page URLs so detail, return and reload preserve it. Do not send q to list/detail APIs or expand the Hub query allowlist. An independent reviewer demonstrated that passing q to the strict Hub detail adapter would otherwise fail.

No usage calculations, identities, collection cadence, provider calls or persistent data change. Empty results remain explicit and clearing the input restores the loaded list. At decision time browser interaction, reload and unchanged grouped accounting remained to be verified; source and readonly HTTP observations were not end-to-end acceptance.
