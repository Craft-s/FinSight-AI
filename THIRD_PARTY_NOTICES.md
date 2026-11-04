# Third-party code and financial material

FinSight AI's original source code and documentation are licensed under the
[MIT License](LICENSE). Third-party packages, archive materials, company
disclosures, trademarks and service data retain their respective notices and
rights. The project license does not relicense these materials or grant access
to external services.

## Included SEC archives

`data/snapshots/` and `data/documents/` contain historical public SEC filing
material obtained from a fixed version of
[EdgarTools](https://github.com/dgunning/edgartools), commit
`3931b22077eee1e0d2b77a8c5a61ab415d51ae0b`.

EdgarTools is distributed under the MIT License. Its copyright and permission
notice is retained in [LICENSE-EDGARTOOLS.txt](data/documents/LICENSE-EDGARTOOLS.txt).
Original SEC URLs, archive locations, timestamps and byte-level hashes are
recorded in the corresponding manifests. See the [JSON archive notes](data/README.md)
and [HTML archive notes](data/documents/README.md).

The upstream software license is not a claim that FinSight AI owns or can
relicense all underlying company disclosures. Preserve their provenance and
attribution when redistributing material, and check the rights applicable to
your intended use.

## A-share providers

Structured financial data is obtained from Eastmoney; company/announcement
indexes and official PDF links are obtained from CNINFO. The source repository
does not include the locally downloaded A-share response archives, PDFs or
production database. Screenshots demonstrate actual application behavior.

The MIT source license does not grant permission to scrape, republish or sell
third-party financial datasets. Follow each provider's applicable terms and
access policies. See [A-share data boundaries](docs/A_SHARE_SUPPORT.md) and
[PDF processing boundaries](docs/A_SHARE_RESEARCH.md).

## Runtime dependencies

Dependencies are referenced in `frontend/package.json`, `frontend/pnpm-lock.yaml`,
`backend/pyproject.toml` and `backend/requirements.lock`. They remain subject to
their own licenses. This repository does not vendor installed dependencies;
retain the required notices when distributing packaged applications or images.
In particular, PDF extraction uses pypdf under BSD-3-Clause.

Cloud-model services, Ollama models and downloaded model weights have their
own terms. Configuring an adapter does not grant model or API usage rights.
