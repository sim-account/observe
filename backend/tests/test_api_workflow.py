"""Exercise the real API from upload through discovery, scenario execution, and export."""
import sys,tempfile,unittest,csv,datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'backend'))
from fastapi.testclient import TestClient
from main import app


def _write_event_log_fixture(path):
    """Deterministic event log used only by the API regression test."""
    rows=[]
    for i in range(120):
        arrival=datetime.datetime(2025,1,6,9)+datetime.timedelta(days=i//8,minutes=(i%8)*30)
        resource='Reviewer-1' if i%2 else 'Reviewer-2'
        for activity,offset,duration in [('Receive request',0,5),('Review request',10,30 if i%2 else 10),('Complete request',60,5)]:
            start=arrival+datetime.timedelta(minutes=offset)
            rows.append({'case_id':str(i),'activity':activity,'resource':resource,'start_time':start.isoformat(),'end_time':(start+datetime.timedelta(minutes=duration)).isoformat(),'channel':'online' if i%3 else 'branch'})
    with path.open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]))
        writer.writeheader();writer.writerows(rows)

def run():
    client=TestClient(app);assert client.get('/api/health').json()=={'status':'ok'}
    assert client.get('/api/session/missing/model-summary').status_code==404
    with tempfile.TemporaryDirectory() as td:
        path=Path(td)/'fixture.csv';_write_event_log_fixture(path)
        uploaded=client.post('/api/upload',files={'file':('fixture.csv',path.read_bytes(),'text/csv')});assert uploaded.status_code==200,uploaded.text
        session=uploaded.json()['session_id'];base=f'/api/session/{session}'
        bad=client.post(base+'/map-columns',json={'session_id':session,'column_mapping':{'case_id':'missing','activity':'activity'}});assert bad.status_code==400
        mapped=client.post(base+'/map-columns',json={'session_id':session,'column_mapping':{'case_id':'case_id','activity':'activity','resource':'resource','start_time':'start_time','end_time':'end_time'}});assert mapped.status_code==200,mapped.text
        for suffix in ['/model-summary','/statistical-learnings','/process-observations','/case-attribute-observations','/observations-recommendations']:
            result=client.get(base+suffix);assert result.status_code==200,(suffix,result.text)
        parameters=client.get(base+'/statistical-learnings').json();assert parameters['activity_durations']
        parsed=client.post(base+'/parse-scenario',json={'text':'Reduce Review request duration by 20%'});assert parsed.status_code==200,parsed.text
        request={'sim_days':7,'warmup_days':2,'num_runs':1,'random_seed':42}
        baseline=client.post(base+'/simulate',json=request);assert baseline.status_code==200,baseline.text
        request['modifications']=[{'type':'time','activity':'Review request','change_type':'reduce_pct','value':20}]
        request['constraints']=[{'type':'activity_presence','text':'Review request must occur','activity':'Review request','operator':'gte','threshold':1}]
        revised=client.post(base+'/simulate',json=request);assert revised.status_code==200,revised.text
        assert revised.json()['modification_audit'] and revised.json()['constraint_results']
        download=client.get(base+'/simulation-log');assert download.status_code==200 and 'case_id' in download.text
        request['modifications']=[{'type':'sequence','from_activity':'unknown','to_activity':'missing','value':50}]
        assert client.post(base+'/simulate',json=request).status_code==422
        # Optional resources and single timestamp parsing remain supported.
        raw='case_id,activity,end_time\n1,A,2025-01-06T09:00:00\n1,B,2025-01-06T10:00:00\n2,A,2025-01-07T09:00:00\n2,B,2025-01-07T10:00:00\n'
        other=client.post('/api/upload',files={'file':('single.csv',raw,'text/csv')}).json()['session_id']
        assert client.post(f'/api/session/{other}/map-columns',json={'session_id':other,'column_mapping':{'case_id':'case_id','activity':'activity','end_time':'end_time'}}).status_code==200
    return {'application_workflow':'passed','optional_resources_single_timestamp':'passed','http_errors':'passed'}
class APIWorkflowTests(unittest.TestCase):
    def test_upload_discovery_simulation_and_export(self):
        self.assertEqual(run()['application_workflow'], 'passed')

if __name__ == '__main__':
    unittest.main()
