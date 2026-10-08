set(MODULE_NAME SkullBaseComparison)

set(MODULE_PYTHON_SCRIPTS
  ${MODULE_NAME}.py
  CorridorAnalysis.py
  EntryProposals.py
  IntegratedPlanning.py
  )

slicerMacroBuildScriptedModule(
  NAME ${MODULE_NAME}
  SCRIPTS ${MODULE_PYTHON_SCRIPTS}
  WITH_GENERIC_TESTS
  )
