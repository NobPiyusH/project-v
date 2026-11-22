-- Stock footage lane (docs/stock-footage-lane.md): Pexels and Pixabay licences, and the source/author/credit fields a stock clip's
-- provenance keeps (Commons clips reuse licence a010). All columns are nullable and unused by every other asset.
INSERT INTO licence (id, name, commercial_use, attribution_required, url, notes) VALUES
 ('00000000-0000-0000-0000-00000000a020', 'Pexels License', true, false, 'https://www.pexels.com/license/',
  'Free for commercial use; no resale of unaltered clips; no implied endorsement; API guideline asks for a Pexels link (credits.txt has it)'),
 ('00000000-0000-0000-0000-00000000a021', 'Pixabay Content License', true, false, 'https://pixabay.com/service/license-summary/',
  'Free for commercial use; no standalone resale; no recognisable trademarks/logos in commercial use; check people and third-party rights'),
 ('00000000-0000-0000-0000-00000000a022', 'Internet Archive (per item: public domain, CC0 or CC BY)', true, true, 'https://archive.org/about/terms.php',
  'Only items whose own licenseurl/rights say public domain, CC0 or CC BY are used; CC BY needs author and licence in the credits');
ALTER TABLE provenance
    ADD COLUMN source_url       text,
    ADD COLUMN source_id        text,
    ADD COLUMN author           text,
    ADD COLUMN attribution_text text,
    ADD COLUMN credit_required  boolean,
    ADD COLUMN fetched_at       timestamptz;
