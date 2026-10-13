from ..agent import Agent, LocalLLM, Toolbox, find_model_file
from ..agent.missing import index_answer
from .missing_files import gather_known


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


def make_agent(state, question: str = "") -> Agent | None:
    # A question the index's own records answer ("how many videos are there",
    # "why is report.pdf missing") is answered at once, without loading the
    # model (up to a minute on the first question) and when the model is
    # missing. The agent answers from the same snapshot that decided this.
    known = gather_known(state) if question else None
    from_index = known is not None and index_answer(question, known) is not None
    llm = None if from_index else get_llm(state)
    if llm is None and not from_index:
        return None
    return Agent(
        llm,
        lambda: Toolbox(state.search_service, state.indexer.vector_store, state.usage_store, state.profile_builder),
        known=(lambda: known) if known is not None else (lambda: gather_known(state)),
    )
