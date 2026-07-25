"""
Checks whether a tool is allowed to run 
proactively. A tool can only fire when:

    state_confidence x gain >= FIRING_THRESHOLD
"""