"""Author-native context labels for existing IR; text/chunk IDs stay unchanged."""
from collections import Counter
from pathlib import Path
import xml.etree.ElementTree as ET


def annotate(packet):
    assets={a['sha256']:a for a in packet['source_assets']};maps={}
    for digest,a in assets.items():
        path=Path(a['path']);locs={}
        if path.suffix.lower() in ('.xml','.nxml'):
            root=ET.fromstring(path.read_bytes())
            for n in root.iter():n.tag=n.tag.rsplit('}',1)[-1]
            def walk(node,loc,sections,container):
                sections=list(sections);container=dict(container)
                if node.tag=='sec':
                    title=node.find('title')
                    if title is not None:sections.append(' '.join(''.join(title.itertext()).split()))
                if node.tag in ('fig','table-wrap'):
                    container=dict(kind=node.tag,id=node.get('id'),label=node.findtext('label'))
                locs[loc]=dict(section_path=sections,container=container,
                    xref_targets=sorted({x.get('rid') for x in node.iter('xref') if x.get('rid')}))
                counts=Counter()
                for child in node:
                    counts[child.tag]+=1;walk(child,loc+'/'+child.tag+'['+str(counts[child.tag])+']',sections,container)
            walk(root,'/article',[],{})
        maps[digest]=locs
    for c in packet['chunks']:
        a=assets[c['author_sha256']]
        c['author_context']=dict(asset_name=Path(a['path']).name,asset_caption=a.get('caption'),
                                 **maps[c['author_sha256']].get(c['locator'],{}))
    packet['quality_metadata_version']='author-context/1'
    return packet
