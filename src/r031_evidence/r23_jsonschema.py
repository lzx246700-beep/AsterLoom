"""Small auditable JSON Schema 2020-12 subset needed by R23 Observation Schema.

Checks required/properties/type/const/enum/pattern/minLength/additionalProperties,
rather than claiming complete JSONSchema Draft2020-12 compliance.
"""
from __future__ import annotations
import re
def _match_type(value,typ):
 return {
  "object":lambda:isinstance(value,dict),
  "array":lambda:isinstance(value,list),
  "string":lambda:isinstance(value,str),
  "number":lambda:type(value) in (int,float),
  "integer":lambda:type(value) is int,
  "boolean":lambda:type(value) is bool,
  "null":lambda:value is None
 }[typ]()
def validate_supported_schema(spec,value,path="$"):
 types=spec.get("type")
 if types:
  choices=types if isinstance(types,list) else [types]
  if not any(_match_type(value,t) for t in choices):
   raise ValueError("SCHEMA_TYPE_MISMATCH:"+path+":"+repr(choices))
 if "const" in spec and value!=spec["const"]:
  raise ValueError("SCHEMA_CONST_MISMATCH:"+path)
 if "enum" in spec and value not in spec["enum"]:
  raise ValueError("SCHEMA_ENUM_MISMATCH:"+path)
 if isinstance(value,str):
  if "minLength" in spec and len(value)<spec["minLength"]:
   raise ValueError("SCHEMA_MIN_LENGTH:"+path)
  if "pattern" in spec and re.search(spec["pattern"],value) is None:
   raise ValueError("SCHEMA_PATTERN:"+path)
 if isinstance(value,dict):
  for k in spec.get("required",[]):
   if k not in value:raise ValueError("SCHEMA_REQUIRED:"+path+"."+k)
  if spec.get("additionalProperties") is False:
   extras=set(value)-set(spec.get("properties",{}))
   if extras:raise ValueError("SCHEMA_ADDITIONAL_KEYS:"+path+":"+repr(sorted(extras)))
  for k,field in spec.get("properties",{}).items():
   if k in value:validate_supported_schema(field,value[k],path+"."+k)
 if isinstance(value,list) and "items" in spec:
  for i,x in enumerate(value):validate_supported_schema(spec["items"],x,path+"["+str(i)+"]")
 return True
