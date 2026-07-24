# diagnostic-analysis-prompt-v1

Use `diagnostic-analysis-data.json` as the only fact source for this analysis.
Write the result to a separate `diagnostic-ai-analysis.json` artifact.

Do not invent facts or imply that a correlation proves causation. Keep SHOW_COUNT,
CLICK_COUNT, and TRANSACTION_AMOUNT audience conclusions separate. Cite the supporting
material ID, metric, dimension, bucket, source value, or script label for every
conclusion. State uncertainty when the fact package has warnings or insufficient data.
