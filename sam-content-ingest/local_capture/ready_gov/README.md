# Ready.gov manual capture

Ready.gov is behind Akamai bot management that 403s automated clients. Capture the
fixed, frozen (2025-09-30), public-domain page/PDF set once in a browser and save it
here, then enable `local_dir: local_capture/ready_gov` in `config/household_readiness.yaml`
and run `sam-ingest ingest household_readiness`.

Filenames must match the seed `id`. Save web pages as "Web Page, HTML Only".

## Web pages → save as
| URL | filename |
|---|---|
| https://www.ready.gov/kit | `build-a-kit.html` |
| https://www.ready.gov/plan | `make-a-plan.html` |
| https://www.ready.gov/plan-form | `communication-plan.html` |
| https://www.ready.gov/evacuation | `evacuation.html` |
| https://www.ready.gov/financial-preparedness | `financial-preparedness.html` |
| https://www.ready.gov/hurricanes | `hurricanes.html` |
| https://www.ready.gov/floods | `floods.html` |
| https://www.ready.gov/wildfires | `wildfires.html` |
| https://www.ready.gov/power-outages | `power-outages.html` |
| https://www.ready.gov/heat | `extreme-heat.html` |

## PDFs → save as
| URL | filename |
|---|---|
| https://www.ready.gov/sites/default/files/2024-05/ready_supply-kit-checklist.pdf | `supply-kit-checklist.pdf` |
| https://www.ready.gov/sites/default/files/2025-06/family-communication-plan_fillable-card.pdf | `communication-plan-card.pdf` |

The captured `.html`/`.pdf` files are git-ignored (they're local inputs). This README is tracked.
