"""bi-modernization modules — BI source extraction and Snowflake AI/BI generation.

Two halves under one package:
  * Extraction  — tableau/, powerbi/, looker/, denodo/, businessobjects/ parsers
                  feeding output/ (inventory, semantic YAML, reports, si_agent).
                  Merged in from the semantic-extraction skill.
  * Generation  — chart_extractor, streamlit_generator, react_generator,
                  agent_builder, preview, and the bim_ui component kit.
"""

__version__ = "0.7.0"

# Version of the semantic-extraction tree merged in at the same module depth.
__extraction_version__ = "0.6.0"
