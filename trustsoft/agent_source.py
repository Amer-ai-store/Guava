from trustsoft.source import SourceError, SECTIONS

class AgentSource:
    def __init__(self, store):
        self.store = store

    def snapshot(self):
        data = self.store.sql_snapshot()
        if data is None:
            raise SourceError('agent_sql_not_verified')
        return {'identity':data['identity'],'catalog':data['catalog'],'relations':data['relations'],
                'schema_state':data.get('metadataMode','discovered'),
                'sections':{k:{'state':'unmapped','rows':[]} for k in SECTIONS}}
