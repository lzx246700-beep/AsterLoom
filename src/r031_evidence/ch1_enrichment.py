"""Deterministic mechanism regime, comparator normalization, Bemis-Murcko scaffold, and debiasing enrichment for Chapter 1 Model A datasets."""
from __future__ import annotations

import math
import re
from collections import defaultdict
from statistics import median
from typing import Any

try:
    from rdkit import Chem
    from rdkit.Chem.Scaffolds import MurckoScaffold

    _HAS_RDKIT = True
except Exception:  # pragma: no cover - graceful fallback in environments without RDKit
    Chem = None  # type: ignore[assignment]
    MurckoScaffold = None  # type: ignore[assignment]
    _HAS_RDKIT = False


USABLE_NUMERIC_TIERS = frozenset({"AUTHOR_NUMERIC", "FIGURE_CONDITIONAL_NUMERIC"})

_BASAL_STIM_RE = re.compile(
    r"^\s*(?:none(?:\s*\((?:basal|37\s*°?\s*c)\))?|basal|control|untreated|d10\s+medium\s*\(control\)|5\.5\s*mM\s*glucose)\s*$",
    re.IGNORECASE,
)

# Active injury, toxicant, metabolic, ischemic, aging, or disease-model stimulation cues.
_STRESS_CUE_RE = re.compile(
    r"(?:"
    r"\bmpp\+|"
    r"fe\(ii\)/?citrate|α-?syn\w*|alpha-?syn\w*|mnps?@sio2|6-?ohda|7-?kc|hg/pa|si/r|"
    r"muc2-/-|a-?1254|els\+era|els\+sora|als\s+tdp43|"
    r"\b(?:"
    r"lps|lipopolysaccharide|h2o2|hydrogen\s+peroxide|rotenone|"
    r"t-?bhp|tert-?butyl\s+hydroperoxide|paraquat|\bpq\b|\bcapt\b|"
    r"ifn-?(?:alpha|beta|β|α|gamma|γ)\d*|\bhfd\b|high[- ]?fat\s+diet|hghf|\bffa\b|"
    r"lipid\s+(?:only|overload)|hyperglycemi\w*|glucose\s+(?:2[5-9]|[3-9]\d)\s*mM|"
    r"(?:2[5-9]|[3-9]\d)\s*mM\s+glucose|glu33|low\s+glucose|cyclocreatine|\bccr\b|veev|"
    r"isoproterenol|(?<!-)\biso\b|starvation|ionizing\s+radiation|7-?ketocholesterol|"
    r"uric\s+acid|tpen|6-?hydroxydopamine|ogdr|cisplatin|cisp|"
    r"post-?thaw|cryopreserv\w*|liquid\s+storage|postovulatory\s+aged|"
    r"zearalenone|\bzea\b|apap|acetaminophen|cocl2|gossypol|atazanavir|saquinavir|"
    r"znso4|zinc\s+chelation|\biav\b|necrotizing\s+enterocolitis|\bnec\b|"
    r"mdi\s+adipogenic|drb18|glut\s+inhibitor|gw6471|vanadyl\s+sulfate|voso4|"
    r"abeta|aβ|aβos|oabeta|amyloid|oxyhb|oxyhemoglobin|3np|quin|"
    r"high\s+glucose|hg\+pal|palmitate|palmitic\s+acid|heat\s+stress|44\s*°?\s*c|"
    r"afb1|aflatoxin|bortezomib|btz|cse|cigarette\s+smoke|cdso4|cadmium|"
    r"d-?gal|d-?galactose|dex|dexamethasone|dox|doxorubicin|elc-?cu|emc|etoh|ethanol|"
    r"glutamate|il-?1(?:beta|β)|tnf-?(?:alpha|α)|tumou?r\s+necrosis\s+factor|ll37|menadione|\bmen\b|naf|fluoride|stz|streptozotocin|"
    r"tgf-?(?:beta|β)|trastuzumab|clioquinol|\bcq\b|ox-?ldl|taurocholate|\bstc\b|"
    r"antimycin|oligomycin|nan3|azide|compound\s*c|"
    r"ogd(?:/r)?|hypoxi\w*|ischemi\w*|reperfusion|\biri\b|mi/r|h/r|\bhi\b|"
    r"tbi|x-?ray|irradiat\w*|\brad\b|ultrasound|vitrif\w*|frozen[–-]thawed|"
    r"aging|aged|\bold\b|app/ps1|apoe4|gdx|prv|pseudorabies|db/db|diabet\w*|\bdm\b|"
    r"fgr|pd\s+group|parkinson|vascular\s+dementia|fasting-?mimicked|"
    r"high-?cholesterol|b16-?f10|tumou?r(?!\s+necrosis\s+factor)|rapid\s+pacing|p301l|9-trans-c18:1|"
    r"stress|model|challenge|injury|toxicity|neurotoxicity|cardiotoxicity|"
    r"oxidative|deprivation|depletion|sepsis|inflammat\w*"
    r")\b"
    r")",
    re.IGNORECASE,
)

# Short exact stress-model tokens appearing as comparator_arm labels (e.g. "V", "P", "NT", "RO", "HC", "PA", "Mn 0.1").
_STRESS_COMP_EXACT_RE = re.compile(
    r"^\s*(?:v|p|nt|ro|hc|pa|dm|hi|cq|btz|cse|fgr|iri|lps|men|naf|ogd|prv|rad|stc|tbi|vit|"
    r"old|aged|model|cell\s+model|db/db|h2o2|etoh|emc|abeta|oabeta|apoe4|afb1|cdso4|d-?gal|"
    r"ua|7kc|pq|capt|iso|ir|hr|b\s*\(hr\)|low|lipid\s+only|als-gluc|v100|drb\s*18|hg/pa|ccr|"
    r"veev\s+alone|ogdr|t-?bhp(?:\s+alone|\s+only)?|6-?ohda|ffa\s+only|tpen\s+only|"
    r"ifn(?:alpha|α2)(?:\s+only)?|cisp|zinc|zea|ulgm|oht|pan|nec|gw6471|mbp\s+group|mt\s*\+\s*mbp\s+group|"
    r"g3\s*\(gossypol\)|mcf-?7\s+md0\.5|a-?1254(?:\s*\d+\s*ug/ml)?|\d+\s*ug/ml\s+a-?1254|"
    r"mn\s*\d*(?:\.\d+)?(?:\s*m?m)?|3np\+quin|hg\+pal|ogd/r|stz\s*\d*(?:\.\d+)?\s*m?m|"
    r"app/ps1-?gdx|wt-?aged)\s*$",
    re.IGNORECASE,
)

_VEHICLE_CONTROL_RE = re.compile(
    r"\b(?:"
    r"control|controls|ctrl|ctl|con|nc|blank|dmso|vehicle|untreated|saline|pbs|hbss|bsa|"
    r"sham|basal|none|non-?egcg|no\s+inhibitor|vector\s+cells|ev|"
    r"same\s+strain\s+without\b[^,;]*|control\s+genotype|glu5\.5|fresh(?:\s*\([^)]*\))?(?:\s+(?:group|sperm(?:\s+reference)?|oocytes?))?|"
    r"0\s*(?:nM|µM|μM|uM|mM|M)\b"
    r")\b",
    re.IGNORECASE,
)

# Cell-context taxonomy for Model A virtual screening:
# strictly separate non-mammalian models, tumor/cancer cell-killing models, and non-tumor/primary/normal tissue models.
_NON_MAMMALIAN_MODEL_RE = re.compile(
    r"(?:"
    r"\b(?:"
    r"yeast|s\.?\s*cerevisiae|saccharomyces|atp2delta|sdh2|schizosaccharomyces|pombe|candida|"
    r"drosophila|fruit\s+fly|fly\s+head\w*|c\.?\s*elegans|caenorhabditis|"
    r"zebrafish|danio\s+rerio|systomus|javaen\s+barb|fish\s+sperm\w*|carp\s+sperm\w*|"
    r"xenopus|e\.?\s*coli|bacteria\w*"
    r")\b"
    r"|(?:Δ|delta)sdh2\b"
    r")",
    re.IGNORECASE,
)

_NON_TUMOR_EXPLICIT_GUARD_RE = re.compile(
    r"\b(?:non[- ]?tumor|non[- ]?cancer|non[- ]?malignant|normal\s+(?:human|mouse|rat|liver|lung|breast|colon|renal|kidney|gastric|prostate|epithelial|fibroblast|tissue))\b",
    re.IGNORECASE,
)

_TUMOR_CANCER_CELL_RE = re.compile(
    r"\b(?:"
    r"hela|a549|mcf-?7|mda-?mb-?(?:231|468|453)|4t1|t47d|bt-?474|sk-?br-?3|zr-?75|"
    r"pc-?3\w*|du145\w*|lncap|22rv1|"
    r"hct-?116|ht-?29|sw480|sw620|lovo|caco-?2|dld-?1|rko|ct26|ls-?180|"
    r"b16(?:-?f10)?|a375|sk-?mel|malme-?3m|"
    r"u87(?:-?mg)?|u251|t98g|gl261|c6\s+glioma|gsc-?\d+|sf-?188|"
    r"hl-?60|k562|jurkat|molt-?4|molm-?13|u937|kg-?1|nb4|raj[iy]|daudi|thp-?1|\baml\b|"
    r"panc-?1|mia\s*paca-?2|bxpc-?3|aspc-?1|sw1990|s2-?013|ar42j|"
    r"skov-?3|a2780|ovcar-?3|hey-?a8|"
    r"ags|mgc-?803|sgc-?7901|hgc-?27|bgc-?823|mnk-?45|mkn-?45|"
    r"mg-?63|saos-?2|u2os|143b|hos|sw-?1353|"
    r"eca-?109|kyse-?\d+|te-?1|"
    r"5637|t24|um-?uc-?3|786-?o|caki-?1|achn|rcc|"
    r"nci-?h\d+|llc(?:\s+(?:spheroid|cells?))?|"
    r"hepg2|hep3b|huh-?7|smmc-?7721|bel-?7402|mhcc-?97[hl]?|hcc-?lm3|sk-?hep-?1|plc/prf/5|"
    r"sh-?sy5y|sk-?n-?sh|imr-?32|n2a|neuro-?2a|pc-?12|"
    r"cal-?27|scc-?\d+|fa-?du|kb\s+cells?|"
    r"cancer|tumou?r(?!\s+necrosis\s+factor)|carcinoma|sarcoma|glioma|glioblastoma|melanoma|chondrosarcoma|"
    r"leukemia|leukaemia|lymphoma|myeloma|xenograft|adenocarcinoma|hepatoma|"
    r"neuroblastoma|pheochromocytoma|osteosarcoma|cholangiocarcinoma|mesothelioma|"
    r"malignant|neoplastic|oncolog\w*"
    r")\b",
    re.IGNORECASE,
)

_NON_TUMOR_CELL_RE = re.compile(
    r"\b(?:"
    r"h9c2|c2c12|huvec|beas-?2b|hbepc|mle-?12|hk-?2|lo2|l-?02|hl-?7702|hek-?293t?|293t|nrk-?52e|"
    r"3t3-?l1|nih/?3t3|mef|mrc-?5|wi-?38|bj\s+(?:normal\s+)?(?:human\s+)?(?:foreskin\s+)?fibroblasts?|bj\s+cells?|"
    r"hacat|arpe-?19|r28|ht22|ht-?22|bv-?2|raw\s*264\.7|rsc96|ac16|smg-?c6|tm3|12z|"
    r"ncm460|ges-?1|rwpe-?1|mcf-?10a|min6|ins-?1|hl-?1|aml12|l6\s+(?:myo\w*|cells?)|l929|vero|cho(?:-?k1)?|cos-?7|"
    r"mn9d|hsg|salivary\s+gland\s+epithelial|ime\s+cells?|hipns?|hcmec/d3|nsc34|dimn|"
    r"mammalian\s+(?:[a-z]+/)*(?:[a-z/]+\s+)?(?:epithelial|primary)\s+cells?|mammalian\s+cells?|animal\s+tissue(?:\s+model)?|"
    r"boar\s+sperm\w*|nucleus\s+pulposus|muscs?|"
    r"primary|cardiomyocytes?|hepatocytes?|neurons?|(?<!non-)neuronal|astrocytes?|microglia|schwann\s+cells?|"
    r"macrophages?|monocytes?|neutrophils?|lymphocytes?|pbmcs?|splenocytes?|spleen|t\s+cells?|b\s+cells?|"
    r"oocytes?|embryos?|blastocysts?|morulae?|sperm|spermatozoa|granulosa|sertoli|leydig|endometriotic|"
    r"fibroblasts?|chondrocytes?|osteoblasts?|osteoclasts?|podocytes?|myotubes?|myoblasts?|"
    r"adipocytes?|keratinocytes?|endothelial(?!\s+growth\s+factor)|bronchial\s+epithelial|mesenchymal|stem\s+cells?|mscs?|ipscs?|islets?|"
    r"pancreatic\s+(?:beta|duct|acinar(?:/pancreatic)?)\s+cells?|acinar/pancreatic\s+cells?|placental\s+organoids?|"
    r"retina\w*|corneal|myocard\w*|hippocamp\w*|cortex|cortical|striat\w*|cerebell\w*|"
    r"liver|hepatic|heart|cardiac|brain|kidney|renal|skeletal\s+muscle|muscles?|lung|pulmonary|intestin\w*|colon\s+tissue|colonocytes?|"
    r"mouse|mice|murine|\brat\b|\brats\b|porcine|piglet|bovine|ovine|rabbit|primate|human\s+(?:primary|normal|tissue|blood|plasma)"
    r")\b",
    re.IGNORECASE,
)


NORMALIZATION_QUALITY_LEVELS = (
    "PROTEIN_OR_CELL_NORMALIZED",
    "TISSUE_WEIGHT_NORMALIZED",
    "RAW_RLU_OR_PCT_CONTROL",
    "UNKNOWN",
)

_NORM_NEGATED_RE = re.compile(
    r"\b(?:no\s+(?:further\s+|cell[- ]?number\s+)?normalization\s+(?:stated|reported)|"
    r"not\s+normalized|unnormalized)\b",
    re.IGNORECASE,
)
_NORM_PROTEIN_CELL_RE = re.compile(
    r"(?:"
    r"protein|gprot\b|ugprot\b|\bprot\b|\bmg\s+pro\b|"
    r"/\s*(?:\d+\s*)?(?:ug|μg|µg|ng|mg)\s*(?:protein|prot\b|pro\b|dna\b)|\bpM/ug\b|\bug\s+of\s+protein\b|"
    r"(?:nmol|pmol|µmol|μmol|umol|mmol|nM|µM|μM|uM)\s*(?:ATP\s*)?/\s*(?:mg|ug|µg|μg)\b|"
    r"\bdna\b|"
    r"cell[- ]?number|cell\s+count|celltracker|dapi\s+nuclear|"
    r"10\^?\d+\s*(?:cells?|sperm\w*)|106\s+cells?|"
    r"(?:50|20|per)\s+oocytes?|oocyte\b|fly\s+head|"
    r"per\s+cell\b|/\s*cell\b|million\s+cells|5\*10\^5\s+cells|20,000\s+cells|100\s+cells"
    r")",
    re.IGNORECASE,
)
_NORM_TISSUE_RE = re.compile(
    r"(?:\b(?:mg|g|kg)\s+(?:\w+\s+)?tissue\b|\bwet\s+weight\b|\bgwwt\b|/g\s+tissue\b|/mg\s+tissue\b|"
    r"(?:nmol|pmol|µmol|μmol|umol|mmol|nM|µM|μM|uM)\s*(?:ATP\s*)?/\s*(?:g|kg)\b)",
    re.IGNORECASE,
)
_NORM_RAW_OR_PCT_RE = re.compile(
    r"(?:"
    r"%|percent|percentage|\bfold\b|\bfolds\b|\brelative\b|\bratio\b|\bversus\b|\bvs\.?\b|"
    r"\brlu\b|\brfu\b|\bmfi\b|\ba\.?\s*u\.?\b|\br\.?\s*u\.?\b|\bo\.?\s*d\.?\b|"
    r"luminescen\w*|luminous|fluorescen\w*|light\s+unit|\barea\b|log2|"
    r"\b(?:control|ctrl|ctr|ctl|con|nc|ck|nt|sham|vehicle|dmso|untreated|blank|none|fresh)\b|"
    r"normalized\s+to\s+(?:0\b|vehicle|control|ctrl|baseline|untreated)|"
    r"raw\s+atp|standard\s+curve|as\s+plotted|atp\s+concentration|\bnmol/well\b|\bpmol\s+atp\b|"
    r"\bnM\s+ATP\b|\bmM\s+in\s+cell\b|^\s*(?:nM|uM|µM|μM|mM|nmol|pmol|µmol|μmol|umol|mmol|ng)"
    r"(?:\s*/\s*(?:mL|ml|L|ul|µl|well))?(?:\s+ATP)?\s*$"
    r")",
    re.IGNORECASE,
)


def classify_normalization_quality(
    normalization: Any,
    assay_method: Any = None,
    unit: Any = None,
) -> str:
    """Categorize ATP assay normalization text into structured quality tiers.

    Returns one of:
    - 'PROTEIN_OR_CELL_NORMALIZED': explicitly normalized to protein, DNA, cell/oocyte/sperm count, or live-cell dye
    - 'TISSUE_WEIGHT_NORMALIZED': normalized to wet/dry tissue mass
    - 'RAW_RLU_OR_PCT_CONTROL': relative %/fold of control, raw luminescence/fluorescence, or unnormalized concentration
    - 'UNKNOWN': missing or uninterpretable normalization label
    """
    norm = str(normalization or "").strip()
    if not norm or norm.upper() in ("NONE", "NULL", "NOT_REPORTED", "N/A", "NA", "AVERAGE"):
        unit_s = str(unit or "").strip()
        if not unit_s:
            return "UNKNOWN"
        norm = unit_s

    if _NORM_NEGATED_RE.search(norm):
        return "RAW_RLU_OR_PCT_CONTROL"
    if _NORM_TISSUE_RE.search(norm):
        return "TISSUE_WEIGHT_NORMALIZED"
    if _NORM_PROTEIN_CELL_RE.search(norm):
        return "PROTEIN_OR_CELL_NORMALIZED"
    if _NORM_RAW_OR_PCT_RE.search(norm):
        return "RAW_RLU_OR_PCT_CONTROL"
    # Fallback to unit/assay_method cues only when normalization text itself was uninformative
    fallback = " ".join(x for x in (str(unit or "").strip(), str(assay_method or "").strip()) if x)
    if fallback:
        if _NORM_TISSUE_RE.search(fallback):
            return "TISSUE_WEIGHT_NORMALIZED"
        if _NORM_PROTEIN_CELL_RE.search(fallback):
            return "PROTEIN_OR_CELL_NORMALIZED"
        if _NORM_RAW_OR_PCT_RE.search(fallback):
            return "RAW_RLU_OR_PCT_CONTROL"
    return "UNKNOWN"


def classify_cell_context(row: dict[str, Any]) -> str:
    """Deterministically separate non-mammalian, tumor/cancer, and non-tumor/primary/normal mammalian models.

    Returns one of:
    - 'NON_MAMMALIAN_MODEL': yeast, invertebrate, fish, amphibian, or bacterial models
    - 'TUMOR_CANCER_LINE': malignant cancer cell lines, tumor xenografts, or oncology models
    - 'NON_TUMOR_PRIMARY_OR_NORMAL': primary cells, gametes/embryos, in-vivo normal tissues, non-malignant lines
    - 'UNKNOWN': missing or uninterpretable biological model
    """
    cell = str(row.get("cell_model") or "").strip()
    stim = str(row.get("stimulation") or "").strip()
    treated = str(row.get("treated_arm") or "").strip()
    comp = str(row.get("comparator_arm") or "").strip()

    primary_scope = cell
    combined_scope = " ".join(x for x in (cell, stim, treated, comp) if x).strip()
    if not combined_scope:
        return "UNKNOWN"

    # Priority 1: Non-mammalian organisms (checked before _NON_TUMOR_CELL_RE so e.g. fish sperm is not treated as mammalian).
    if primary_scope and _NON_MAMMALIAN_MODEL_RE.search(primary_scope):
        return "NON_MAMMALIAN_MODEL"

    # Explicit non-tumor guard on cell_model (e.g. "normal human lung epithelial cells")
    if primary_scope and _NON_TUMOR_EXPLICIT_GUARD_RE.search(primary_scope) and not _TUMOR_CANCER_CELL_RE.search(primary_scope):
        return "NON_TUMOR_PRIMARY_OR_NORMAL"

    # Check cell_model first for tumor/cancer identity (e.g. HepG2, SH-SY5Y, A549, HeLa, B16-F10, DU145AcT, PC-3AcT)
    if primary_scope and _TUMOR_CANCER_CELL_RE.search(primary_scope):
        return "TUMOR_CANCER_LINE"

    sc_resolved = bool(
        primary_scope
        and re.search(r"\bscs?\b", primary_scope, re.IGNORECASE)
        and re.search(r"\b(?:schwann|sertoli|stem)\s+cells?\b", combined_scope, re.IGNORECASE)
    )
    if primary_scope and (_NON_TUMOR_CELL_RE.search(primary_scope) or sc_resolved):
        # Even if cell_model is mouse/mice, a tumor xenograft cue in stimulation/arms marks it tumor context
        if _TUMOR_CANCER_CELL_RE.search(combined_scope) and not _NON_TUMOR_EXPLICIT_GUARD_RE.search(combined_scope):
            return "TUMOR_CANCER_LINE"
        return "NON_TUMOR_PRIMARY_OR_NORMAL"

    # Fallback to combined context when cell_model is absent/abbreviated
    if _NON_MAMMALIAN_MODEL_RE.search(combined_scope):
        return "NON_MAMMALIAN_MODEL"
    if _TUMOR_CANCER_CELL_RE.search(combined_scope) and not _NON_TUMOR_EXPLICIT_GUARD_RE.search(combined_scope):
        return "TUMOR_CANCER_LINE"
    if _NON_TUMOR_CELL_RE.search(combined_scope):
        return "NON_TUMOR_PRIMARY_OR_NORMAL"
    return "UNKNOWN"


# Dose parser: converts literal dose string (including scientific notation) into micromolar (µM) when unambiguous molarity is stated.
_DOSE_SCI_BASE10_RE = re.compile(
    r"(?:(?P<coeff>\d+(?:\.\d+)?)\s*[×x*]\s*)?10\s*(?:\^\s*(?P<exp_caret>[+-−–]?\d+)|(?P<exp_unicode>[−–]\d+))\s*"
    r"(?P<unit>pM|nM|µM|μM|uM|µmol/L|μmol/L|umol/L|mM|mmol/L|M|mol/L)\b",
    re.IGNORECASE,
)
_DOSE_MOLAR_RE = re.compile(
    r"(?P<val>\d+(?:\.\d+)?(?:[eE][+-−–]?\d+)?)\s*(?P<unit>pM|nM|µM|μM|uM|µmol/L|μmol/L|umol/L|mM|mmol/L|M|mol/L)\b",
    re.IGNORECASE,
)

# Exposure time parser: converts literal duration string into hours.
_TIME_RE = re.compile(
    r"(?P<val>\d+(?:\.\d+)?)\s*(?P<unit>min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days|wk|wks|week|weeks)\b",
    re.IGNORECASE,
)


def _scale_molar_unit_to_uM(val: float, raw_unit: str) -> float | None:
    unit = raw_unit.lower().replace("µ", "u").replace("μ", "u")
    if unit == "pm":
        return round(val * 1e-6, 12)
    if unit == "nm":
        return round(val * 1e-3, 12)
    if unit in ("um", "umol/l"):
        return round(val, 12)
    if unit in ("mm", "mmol/l"):
        return round(val * 1e3, 12)
    if unit in ("m", "mol/l"):
        return round(val * 1e6, 12)
    return None


def parse_dose_uM(dose_literal: Any) -> float | None:
    """Parse molar dose literal into micromolar (µM). Returns None for mass/in-vivo, ranges, or unreported doses."""
    if dose_literal in (None, "", "None", "NOT_REPORTED_IN_FIG"):
        return None
    s = str(dose_literal).strip()
    if not s:
        return None
    # Skip ranges like "1-100 μM" where single point dose is not encoded in the text
    if re.search(
        r"(?<![eE\^×x*])\b\d+(?:\.\d+)?\s*[-–—]\s*\d+(?:\.\d+)?\s*"
        r"(?:pM|nM|µM|μM|uM|µmol/L|μmol/L|umol/L|mM|mmol/L|M|mol/L)\b",
        s,
        re.IGNORECASE,
    ):
        return None
    m_sci = _DOSE_SCI_BASE10_RE.search(s)
    if m_sci:
        coeff_raw = m_sci.group("coeff")
        coeff = float(coeff_raw) if coeff_raw is not None else 1.0
        exp_raw = (m_sci.group("exp_caret") or m_sci.group("exp_unicode") or "0").replace("−", "-").replace("–", "-")
        val = coeff * (10.0 ** int(exp_raw))
        return _scale_molar_unit_to_uM(val, m_sci.group("unit"))
    m = _DOSE_MOLAR_RE.search(s)
    if not m:
        return None
    val_str = m.group("val").replace("−", "-").replace("–", "-")
    val = float(val_str)
    return _scale_molar_unit_to_uM(val, m.group("unit"))


def parse_exposure_hours(time_literal: Any) -> float | None:
    """Parse exposure duration literal into hours. Returns None if unreported."""
    if time_literal in (None, "", "None", "NOT_REPORTED_IN_FIG"):
        return None
    s = str(time_literal).strip()
    if not s:
        return None
    m = _TIME_RE.search(s)
    if not m:
        return None
    val = float(m.group("val"))
    unit = m.group("unit").lower()
    if unit.startswith("min"):
        return round(val / 60.0, 4)
    if unit.startswith("h"):
        return round(val, 4)
    if unit.startswith("d"):
        return round(val * 24.0, 4)
    if unit.startswith("w"):
        return round(val * 24.0 * 7.0, 4)
    return None


def compute_murcko_scaffold_smiles(smiles: str | None) -> str:
    """Compute canonical RDKit Bemis-Murcko scaffold SMILES.

    - Cyclic molecules: canonical generic/standard Bemis-Murcko scaffold SMILES (chirality-stripped).
    - Acyclic molecules (empty Murcko scaffold): fallback to canonical SMILES of the molecule itself.
    - Missing/invalid SMILES or RDKit unavailable: empty string "".
    """
    if not smiles or not str(smiles).strip():
        return ""
    if not _HAS_RDKIT or Chem is None or MurckoScaffold is None:
        return ""
    mol = Chem.MolFromSmiles(str(smiles).strip())
    if mol is None:
        return ""
    scaffold = MurckoScaffold.MurckoScaffoldSmiles(mol=mol, includeChirality=False)
    if scaffold:
        return scaffold
    return Chem.MolToSmiles(mol, canonical=True)


def classify_paradigm_and_comparator(row: dict[str, Any]) -> tuple[str, str]:
    """Deterministically classify (paradigm_regime, comparator_arm_type) from row context.

    paradigm_regime: 'BASAL' | 'STRESS_RESCUE' | 'UNKNOWN'
    comparator_arm_type: 'VS_VEHICLE_CONTROL' | 'VS_STRESS_MODEL' | 'OTHER'
    """
    stim = str(row.get("stimulation") or "").strip()
    comp = str(row.get("comparator_arm") or "").strip()
    treated = str(row.get("treated_arm") or "").strip()
    cell = str(row.get("cell_model") or "").strip()

    stim_is_explicit_basal = bool(stim) and bool(_BASAL_STIM_RE.match(stim))
    stim_has_stress = bool(stim) and not stim_is_explicit_basal and (
        bool(_STRESS_CUE_RE.search(stim)) or bool(_STRESS_COMP_EXACT_RE.match(stim))
    )

    comp_has_stress = bool(comp) and (
        bool(_STRESS_COMP_EXACT_RE.match(comp)) or bool(_STRESS_CUE_RE.search(comp))
    )
    # Guard against false stress triggers in basal vehicle labels like "0 mM sodium butyrate" or "non-EGCG sample, no inhibitor"
    if comp_has_stress and re.search(r"\bno\s+inhibitor\b", comp, re.IGNORECASE):
        comp_has_stress = False
    if comp_has_stress and re.match(r"^\s*0\s*mM\s+sodium\s+butyrate\s*$", comp, re.IGNORECASE):
        comp_has_stress = False
    if comp_has_stress and re.match(r"^\s*None,\s+same\s+glucose-containing", comp, re.IGNORECASE):
        comp_has_stress = False

    # Also check treated_arm / cell_model for clear rescue context when stimulation was omitted (e.g. PMC10049002 P301L, PMC10229463 PD group, post-thaw/storage/aged rescue)
    treated_has_stress = bool(treated) and bool(
        re.search(
            r"\b(?:pd\s+\+|aged\s+\+|lps\s*\+|h2o2\s*\+|ogd(?:/r)?\s*\+|rotenone\s*\+|antimycin|oligomycin|nan3|compound\s*c|"
            r"post-?thaw|cryopreserv\w*|liquid\s+storage|postovulatory\s+aged|mbp\s*\d+)\b",
            treated,
            re.IGNORECASE,
        )
    )
    cell_has_storage_or_aged_stress = bool(cell) and bool(
        re.search(r"\b(?:post-?thaw|cryopreserv\w*|liquid\s+storage|postovulatory\s+aged)\b", cell, re.IGNORECASE)
    )
    # Fresh sperm reference self-check without post-thaw rescue stays basal
    is_fresh_self_check = (
        bool(re.search(r"\bfresh\b", comp, re.IGNORECASE))
        and bool(re.search(r"\bfresh\b", treated, re.IGNORECASE))
        and not treated_has_stress
    )
    if is_fresh_self_check:
        cell_has_storage_or_aged_stress = False

    comp_is_vehicle = bool(comp) and bool(_VEHICLE_CONTROL_RE.search(comp)) and not comp_has_stress

    # Determine paradigm_regime
    if stim_has_stress or comp_has_stress or treated_has_stress or cell_has_storage_or_aged_stress:
        paradigm = "STRESS_RESCUE"
    elif stim_is_explicit_basal or comp_is_vehicle:
        paradigm = "BASAL"
    elif not stim and not comp:
        paradigm = "UNKNOWN"
    else:
        # Check if cell_model itself indicates an active stress/disease model with a non-vehicle comparator
        if _STRESS_CUE_RE.search(cell) and not comp_is_vehicle:
            paradigm = "STRESS_RESCUE"
        else:
            paradigm = "UNKNOWN"

    # Determine comparator_arm_type
    if not comp:
        comp_type = "OTHER"
    elif comp_has_stress:
        comp_type = "VS_STRESS_MODEL"
    elif paradigm == "STRESS_RESCUE":
        # Under an active stress stimulation (e.g. B16-F10 CM, APP/PS1, H/R), a matched vehicle/0-dose arm is a stress-model comparator
        comp_type = "VS_STRESS_MODEL"
    elif comp_is_vehicle:
        comp_type = "VS_VEHICLE_CONTROL"
    else:
        comp_type = "OTHER"

    return paradigm, comp_type


def _parent_group_key(row: dict[str, Any]) -> str:
    ik = str(row.get("inchikey") or "").strip()
    if ik:
        return ik
    parent = str(row.get("canonical_parent") or "").strip().casefold()
    if parent:
        return f"NAME:{parent}"
    return f"REC:{row.get('record_id') or ''}"


def _is_usable_numeric_atp(row: dict[str, Any]) -> bool:
    return (
        row.get("model_usable_tier") in USABLE_NUMERIC_TIERS
        and bool(row.get("is_natural_or_endogenous"))
        and row.get("endpoint") == "ATP_CONTENT"
    )


def _select_median_dose_representative(bucket: list[dict[str, Any]]) -> dict[str, Any]:
    """Pick a deterministic median-dose representative from a bucket of rows."""
    if len(bucket) == 1:
        return bucket[0]
    dosed = [(parse_dose_uM(r.get("dose")), r) for r in bucket]
    valid_doses = [d for d, _ in dosed if d is not None]
    if valid_doses:
        med = median(valid_doses)
        # Prefer rows with valid numeric dose closest to median log-dose, tie-break by record_id
        return min(
            bucket,
            key=lambda r: (
                0 if parse_dose_uM(r.get("dose")) is not None else 1,
                abs(math.log10(max(parse_dose_uM(r.get("dose")) or med, 1e-9)) - math.log10(max(med, 1e-9))),
                str(r.get("record_id") or ""),
            ),
        )
    # Fallback when doses are non-molar or unreported: median log2fc_est, tie-break by record_id
    l2s = [float(r["log2fc_est"]) for r in bucket if r.get("log2fc_est") is not None]
    med_y = median(l2s) if l2s else 0.0
    return min(
        bucket,
        key=lambda r: (
            abs(float(r.get("log2fc_est") or 0.0) - med_y),
            str(r.get("record_id") or ""),
        ),
    )


def _select_cap_k_for_parent(parent_rows: list[dict[str, Any]], cap: int = 3) -> set[str]:
    """Deterministically select up to `cap` representative record_ids for one parent molecule.

    Priority order:
    1. Cover distinct paradigm_regimes (BASAL & STRESS_RESCUE) and distinct papers first.
    2. Within each (paper_id, paradigm_regime) stratum, pick the median-dose representative.
    3. Fill remaining slots up to `cap` from remaining strata or spread doses deterministically.
    """
    if len(parent_rows) <= cap:
        return {str(r["record_id"]) for r in parent_rows}

    # Group by (paper_id, paradigm_regime)
    strata: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in parent_rows:
        strata[(str(r.get("paper_id") or ""), str(r.get("paradigm_regime") or "UNKNOWN"))].append(r)

    # Sort strata keys deterministically
    strata_keys = sorted(strata.keys())
    selected_ids: list[str] = []
    used_papers: set[str] = set()
    used_regimes: set[str] = set()

    # Pass 1: maximize regime diversity + paper diversity
    for target_regime in ("BASAL", "STRESS_RESCUE", "UNKNOWN"):
        if len(selected_ids) >= cap:
            break
        candidates = [k for k in strata_keys if k[1] == target_regime]
        if not candidates:
            continue
        # Prefer a paper not yet selected, then larger stratum (more informative), then paper_id
        best_key = min(
            candidates,
            key=lambda k: (1 if k[0] in used_papers else 0, -len(strata[k]), k[0]),
        )
        rep = _select_median_dose_representative(strata[best_key])
        rid = str(rep["record_id"])
        if rid not in selected_ids:
            selected_ids.append(rid)
            used_papers.add(best_key[0])
            used_regimes.add(best_key[1])

    # Pass 2: cover additional distinct papers not yet selected
    for k in sorted(strata_keys, key=lambda x: (-len(strata[x]), x[0], x[1])):
        if len(selected_ids) >= cap:
            break
        if k[0] in used_papers:
            continue
        rep = _select_median_dose_representative(strata[k])
        rid = str(rep["record_id"])
        if rid not in selected_ids:
            selected_ids.append(rid)
            used_papers.add(k[0])

    # Pass 3: cover additional distinct (paper, regime) strata
    for k in sorted(strata_keys, key=lambda x: (-len(strata[x]), x[0], x[1])):
        if len(selected_ids) >= cap:
            break
        rep = _select_median_dose_representative(strata[k])
        rid = str(rep["record_id"])
        if rid not in selected_ids:
            selected_ids.append(rid)

    # Pass 4: if all rows come from < cap strata (e.g. single paper & regime dose curve),
    # pick remaining rows evenly across sorted dose / log2fc order
    if len(selected_ids) < cap:
        remaining = [r for r in parent_rows if str(r["record_id"]) not in selected_ids]
        remaining.sort(
            key=lambda r: (
                0 if parse_dose_uM(r.get("dose")) is not None else 1,
                parse_dose_uM(r.get("dose")) if parse_dose_uM(r.get("dose")) is not None else float("inf"),
                float(r.get("log2fc_est") or 0.0),
                str(r.get("record_id") or ""),
            )
        )
        need = cap - len(selected_ids)
        if len(remaining) <= need:
            selected_ids.extend(str(r["record_id"]) for r in remaining)
        else:
            # Spread picks across low/high quantiles of remaining sorted rows
            for i in range(need):
                idx = round(i * (len(remaining) - 1) / max(need - 1, 1)) if need > 1 else len(remaining) // 2
                rid = str(remaining[idx]["record_id"])
                if rid not in selected_ids:
                    selected_ids.append(rid)
            for r in remaining:
                if len(selected_ids) >= cap:
                    break
                rid = str(r["record_id"])
                if rid not in selected_ids:
                    selected_ids.append(rid)

    return set(selected_ids[:cap])


def enrich_ch1_rows(
    rows: list[dict[str, Any]],
    cap: int = 3,
    recompute_cell_context: bool = False,
) -> list[dict[str, Any]]:
    """Attach standardized regime, cell_context, comparator_arm_type, normalization_quality, murcko_scaffold_smiles, mol_sample_weight, and mol_cap3_selected.

    Strictly preserves all existing fields, SHA hashes, independent_gold, and training_approved.
    """
    enriched: list[dict[str, Any]] = []
    for r in rows:
        row = dict(r)
        paradigm, comp_type = classify_paradigm_and_comparator(row)
        row["paradigm_regime"] = paradigm
        row["comparator_arm_type"] = comp_type
        row["normalization_quality"] = classify_normalization_quality(
            row.get("normalization"),
            assay_method=row.get("assay_method"),
            unit=row.get("unit"),
        )
        explicit_ctx = str(row.get("cell_context") or "").strip().upper()
        if not recompute_cell_context and explicit_ctx in (
            "NON_TUMOR_PRIMARY_OR_NORMAL",
            "TUMOR_CANCER_LINE",
            "NON_MAMMALIAN_MODEL",
        ):
            row["cell_context"] = explicit_ctx
        else:
            row["cell_context"] = classify_cell_context(row)

        if _HAS_RDKIT:
            row["murcko_scaffold_smiles"] = compute_murcko_scaffold_smiles(row.get("smiles"))
        else:
            row["murcko_scaffold_smiles"] = str(row.get("murcko_scaffold_smiles") or "")

        row["mol_sample_weight"] = None
        row["mol_cap3_selected"] = False
        enriched.append(row)

    # Compute debiasing weights & Mol_Cap3 selection strictly over usable numeric natural ATP_CONTENT rows
    by_parent: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in enriched:
        if _is_usable_numeric_atp(row):
            by_parent[_parent_group_key(row)].append(row)

    for parent_rows in by_parent.values():
        weight = round(1.0 / len(parent_rows), 6)
        selected_ids = _select_cap_k_for_parent(parent_rows, cap=cap)
        for row in parent_rows:
            row["mol_sample_weight"] = weight
            row["mol_cap3_selected"] = str(row["record_id"]) in selected_ids

    return enriched
