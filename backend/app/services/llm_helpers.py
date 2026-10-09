from ..agent import Agent, LocalLLM, Toolbox, find_model_file


def find_llm_file(state):
    # Looked up again while missing, so restoring models/llm
    # turns Ask on without restarting the app.
    if state.llm_file is None:
        state.llm_file = find_model_file()
    return state.llm_file


def load_error_message(error: Exception) -> str:
    """What Ask tells the user when the model file would not load."""
    return f"Ask mode's local language model could not be loaded ({type(error).__name__}: {error}). Extract IntelliFile-windows.zip again, completely, then ask again. The details are in backend.log in %LOCALAPPDATA%\\IntelliFile\\logs."


def get_llm(state):
    """The loaded model (loading it now if needed), or None when the file is
    missing. A load failure is remembered in `state.llm_error` for
    /ask/status, and cleared by the next load that works."""
    with state.llm_lock:
        if state.llm is None and find_llm_file(state) is not None:
            try:
                state.llm = LocalLLM(state.llm_file)
            except Exception as e:
                state.llm_error = load_error_message(e)
                raise
            state.llm_error = None
        return state.llm


def make_agent(state) -> Agent | None:
    llm = get_llm(state)
    if llm is None:
        return None
    return Agent(
        llm,
        lambda: Toolbox(state.search_service, state.indexer.vector_store, state.usage_store, state.profile_builder),
    )
