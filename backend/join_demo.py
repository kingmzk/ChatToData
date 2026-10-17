"""Add a reproducible two-table synthetic demo without changing the original source."""
import json, sqlite3
from pathlib import Path

def add_join_demo(directory):
    directory = Path(directory)
    config_path = directory / 'runtime.json'
    config = json.loads(config_path.read_text(encoding='utf-8'))
    conn = sqlite3.connect(directory / config['source_database'])
    with conn:
        conn.execute('CREATE TABLE IF NOT EXISTS demo_customers (tenant_id TEXT NOT NULL,customer_id TEXT NOT NULL,region TEXT NOT NULL,segment TEXT NOT NULL,PRIMARY KEY(tenant_id,customer_id))')
        conn.execute('CREATE TABLE IF NOT EXISTS demo_join_sales (id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,customer_id TEXT NOT NULL,amount TEXT NOT NULL,year TEXT NOT NULL)')
        for tenant in ('tenant_a','tenant_b'):
            for index in range(1,21):
                region='North' if index<=10 else 'South'
                segment='Business' if index%2 else 'Consumer'
                customer=str(index)
                conn.execute('INSERT OR IGNORE INTO demo_customers VALUES (?,?,?,?)',(tenant,customer,region,segment))
                for year in (2023,2024):
                    amount=(50 if region=='North' else 40)
                    if year==2024: amount += 20 if region=='North' else 10
                    if tenant=='tenant_b': amount=1000000
                    conn.execute('INSERT INTO demo_join_sales VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET amount=excluded.amount',(f'{tenant}-{customer}-{year}',tenant,customer,str(amount),str(year)))
    conn.close()
    families=['TOTAL','TREND','COMPARISON','CONTRIBUTION']
    common={'table':'demo_join_sales','base_grain':'sale','protected_entity_column':'customer_id','min_entities':5,'allowed_dimensions':['year','region','segment']}
    catalog={
        'source_id':'demo_join_sales','release_id':'join_demo_v1','epoch':1,
        'tables':{
            'demo_join_sales':{'table_name':'demo_join_sales','grain':'sale','columns':{'id':'STRING','tenant_id':'STRING','customer_id':'STRING','amount':'DECIMAL','year':'STRING'}},
            'demo_customers':{'table_name':'demo_customers','grain':'customer','primary_key':['tenant_id','customer_id'],'columns':{'tenant_id':'STRING','customer_id':'STRING','region':'STRING','segment':'STRING'}}},
        'measures':{
            'revenue':dict(common,id='revenue',name='Revenue',description='Total synthetic sale amounts',column='amount',aggregation='SUM',unit='USD',allowed_families=families),
            'average_sale':dict(common,id='average_sale',name='Average Sale Value',description='Approved average amount per sale',column='amount',aggregation='AVG',unit='USD',allowed_families=['TOTAL','AVERAGE','COMPARISON']),
            'customer_count':dict(common,id='customer_count',name='Customer Count',description='Distinct purchasing customers',column='customer_id',aggregation='COUNT_DISTINCT',unit='customers',allowed_families=['TOTAL','COMPARISON'])},
        'dimensions':{
            'year':{'id':'year','name':'Year','table':'demo_join_sales','column':'year','data_type':'STRING'},
            'region':{'id':'region','name':'Customer Region','table':'demo_customers','column':'region','data_type':'STRING'},
            'segment':{'id':'segment','name':'Customer Segment','table':'demo_customers','column':'segment','data_type':'STRING'}},
        'joins':[{'from_table':'demo_join_sales','to_table':'demo_customers','from_column':'customer_id','to_column':'customer_id','cardinality':'MANY_TO_ONE','join_type':'INNER'}],
        'default_period_dimension':'year'
    }
    if not any(c['source_id']=='demo_join_sales' for c in config['catalogs']):
        config['catalogs'].append(catalog)
    grants=config['tenant_source_permissions'].setdefault('tenant_a',[])
    if 'demo_join_sales' not in grants:grants.append('demo_join_sales')
    config_path.write_text(json.dumps(config,indent=2),encoding='utf-8')
    return catalog

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--directory',default='browser-demo')
    add_join_demo(parser.parse_args().directory)
    print('Added demo_join_sales: sales + customers; tenant-isolated synthetic data.')
