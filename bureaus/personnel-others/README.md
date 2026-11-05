# personnel-others（五局·美军干部局、六局·公检法干部局，原人事四局）

归局（docs/PRODUCT.md 第五节）：参联会、各军种、各作战司令部、国家安全局归五局；最高法院、联邦调查局归六局；联邦通信委员会、美联储理事会、国家情报总监、中央情报局归三局。人物记录由 `python -m zuzhibu.importer` 导入 `data/people/`。

## Roster notes

Roster v0.1. Updated 2026-10-17.

[roster/roster.csv](roster/roster.csv) contains 34 people, one person per row. [roster/sources.md](roster/sources.md) records each person's official sources and retrieval date.

## Scope

- Chairman and vice chairman of the Joint Chiefs of Staff.
- Uniformed service chiefs of the Army, Navy, Air Force, Marine Corps, Space Force and Coast Guard.
- Commanders of U.S. Cyber Command, U.S. Space Command, U.S. Pacific Command and U.S. Strategic Command; director of the National Security Agency.
- All nine current Supreme Court justices.
- FCC chairman and sitting commissioners.
- Federal Reserve chairman and all sitting Board governors, including both vice chairs.
- Director of National Intelligence, CIA director and FBI director.

Joshua M. Rudd's Cyber Command and NSA offices share one row. The FCC and Federal Reserve chairmen are each counted once within their commission or Board membership.

## CSV fields

`english_name,position_en,organization_en,since,official_bio_url,official_portrait_url,notes`

Military rank is included in position_en. Since is the start of the listed office, using YYYY-MM-DD or YYYY-MM where only the month is established. Continuous Board membership dates are used for governors whose title is Governor; vice-chair rows use the start of that vice-chair office. Unknown dates and unverified portrait addresses are empty.

All fields and documentation use English. Acting appointments use `Acting` in notes. A pending nominee uses `Nomination pending confirmation, nominated YYYY-MM-DD`; no pending nominee is included in this snapshot. Official .gov appointment and witness records provide accessible alternatives to agency biographies.

## Known gaps

- Christopher C. LaNeve is acting Chief of Staff of the Army. The acting appointment date is unverified, so since is empty.
- Olivia Trusty's start is recorded as 2025-06; the exact day was not verified in the retrieved official records.
- FCC evidence identifies three sitting commissioners against five authorized seats; two seats have no identified sitting member in this roster.
- Official portrait addresses remain empty for 14 people: Dan Caine; Christopher J. Mahoney; Kenneth S. Wilsbach; Eric M. Smith; Douglas A. Schiess; Kevin E. Lunday; Stephen N. Whiting; Samuel J. Paparo, Jr.; Richard A. Correll; Brendan Carr; Anna M. Gomez; Olivia Trusty; Jay Clayton; Kash Patel.

The command's current website names U.S. Pacific Command and records a June 2026 change from U.S. Indo-Pacific Command. The older name remains in the April 2026 Senate witness record linked in the CSV; the current-name and tenure sources are linked in sources.md.

## Updating

Reopen the official membership and leadership records, update the affected person and office dates, and record the new retrieval date in sources.md. Recheck concurrent roles and vacancies when a member leaves or a successor takes office.
