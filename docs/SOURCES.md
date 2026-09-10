# Официальные источники

## JPL

- JPL Horizons manual:
  https://ssd.jpl.nasa.gov/horizons/manual.html
- Horizons API:
  https://ssd-api.jpl.nasa.gov/doc/horizons.html
- SPICE SPK required reading:
  https://naif.jpl.nasa.gov/pub/naif/toolkit_docs/C/req/spk.html
- SBDB Query API:
  https://ssd-api.jpl.nasa.gov/doc/sbdb_query.html
- Small-body orbital-data files:
  https://ssd.jpl.nasa.gov/sb/orbits.html
- Yao et al. (2025), A&A 701 A15, Table 1 — SB441-N16 massive perturber list and
  `GM/GM_sun` values:
  https://www.aanda.org/articles/aa/full_html/2025/09/aa54652-25/aa54652-25.html

## Gaia

- Gaia FPR documentation:
  https://gea.esac.esa.int/archive/documentation/FPR/
- `gaiafpr.sso_observation` metadata and DOI:
  https://gaia.aip.de/metadata/gaiafpr/sso_observation/
- `gaiafpr.sso_source` metadata and DOI:
  https://gaia.aip.de/metadata/gaiafpr/sso_source/
- SSO observations DOI:
  https://doi.org/10.17876/gaia/fpr.1/8
- SSO state vectors and covariance DOI:
  https://doi.org/10.17876/gaia/fpr.1/9

Gaia FPR is published under CC BY-NC 3.0 IGO. Preserve the official Gaia/DPAC
acknowledgement, DOI, provenance and non-commercial license notice in derived
reports and datasets.

## Minor Planet Center

- MPC observations API:
  https://docs.minorplanetcenter.net/mpc-ops-docs/apis/get-obs/

MPC is an advanced observation-level phase. It is not required for the first
reproducible core.

## Citation rule

The eventual report must distinguish:

- raw observational data;
- fitted/catalogue products;
- model-derived ephemerides;
- locally generated baselines and labels.

Do not describe Horizons trajectories as raw telescope measurements.
