# Hook vazio proposital.
#
# O projeto tem um arquivo local chamado panel.py. O PyInstaller tambem possui
# hook para a biblioteca externa "panel" (HoloViz), que puxa dependencias enormes
# como pandas/scipy. Este hook local tem prioridade e impede esse falso positivo.
