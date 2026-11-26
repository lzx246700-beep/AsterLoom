"""R15 Europe PMC official author JATS acquisition; source-only and license-gated."""
import hashlib,re,xml.etree.ElementTree as ET
from pathlib import Path
from . import repository as repo,persistent_queue,source_intake
from .provider_client import read_official

def acquire(workspace,db,batch_id,max_sources=10,reader=read_official,priority_domain=None,allow_pmcids=None):
    # Every page hit is registered by discovery before cursor commit. This
    # compatibility entry drains only pending items from the requested batch.
    from .r30_ledger import connect
    from .r30_pipeline import drain
    from contextlib import closing
    with closing(connect(workspace)) as c:
        ids=[r[0] for r in c.execute("SELECT DISTINCT h.paper_id FROM r30_hit h JOIN r15_paper_hit p ON p.hit_id=h.legacy_hit_id WHERE p.batch_id=?",(batch_id,))]
        if allow_pmcids is not None:
            allowed=set(allow_pmcids)
            ids=[p for p in ids if any(r[0] in allowed for r in c.execute("SELECT value FROM r30_alias WHERE paper_id=? AND namespace='pmcid'",(p,)))]
    result=drain(workspace,max_sources,reader=reader if reader is not read_official else None,paper_ids=ids)
    result['batch_id']=batch_id
    return result
