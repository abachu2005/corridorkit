set(MODULE_NAME SkullBaseComparison)

set(MODULE_PYTHON_SCRIPTS
  ${MODULE_NAME}.py
  CorridorAnalysis.py
  EntryProposals.py
  IntegratedPlanning.py
  )

set(MODULE_PYTHON_RESOURCES
  Resources/Icons/SkullBaseComparison.png
  )

slicerMacroBuildScriptedModule(
  NAME ${MODULE_NAME}
  SCRIPTS ${MODULE_PYTHON_SCRIPTS}
  RESOURCES ${MODULE_PYTHON_RESOURCES}
  WITH_GENERIC_TESTS
  )
