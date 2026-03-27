class BaseEnvironment:
    def __init__(self, env_id, num_agents):
        self.env_id = env_id
        self.num_agents = num_agents

    def reset(self) -> dict:
        """Return initial environment graph (nodes+edges) and state model."""
        raise NotImplementedError

    def step(self, action: str, obj_id: int, target_id: int = None) -> tuple:
        """
        Execute a canonical action on object(s). Return:
          - new graph (dict),
          - new ObjectStateModel,
          - reward,
          - done,
          - info (including error_type if action failed).
        """
        raise NotImplementedError

    def close(self):
        pass