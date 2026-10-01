# spreadsheets (SYNTHETIC)

CSV exports standing in for a workbook. The workbook is the system of record for the credit risk
pack; these CSVs are what actually reaches downstream systems, which is the point: the formulas,
the merged cells, the hidden rows and the cell comments are gone, and `FORMULAS.yaml` is a
hand-maintained sidecar that can drift from the data it describes.

Read `FORMULAS.yaml` before trusting any column here.
