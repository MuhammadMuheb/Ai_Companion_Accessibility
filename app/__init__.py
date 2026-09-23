"""AI Companion (Nova) package."""

import warnings

# soundcard warns about a harmless gap at the start of every recording; keep logs readable
warnings.filterwarnings("ignore", message="data discontinuity in recording")
