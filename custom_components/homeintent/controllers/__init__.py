"""Domain controllers of the conversation (7.7, B4).

``conversation.py`` takes the input, builds the turn context, lets the
arbiter decide which meaning applies and hands the turn to one controller.
Each controller receives only the dependencies it needs (explicit
constructor arguments; a narrow ``Protocol`` for runtime services) and
never imports ``conversation.py``. Dependency direction (architecture
test): conversation → controllers → meaning/domain → grounding → plans →
policy/executor.
"""
