/*
 * tjunction_resolver.c — T-junction resolver in C
 *
 * Python API:
 *   resolve_tjunctions(raw_quads) -> (vertices, polygons)
 *     raw_quads : list of ([[x,y,z]x4], side_int, color_int)
 *     vertices  : list of [x,y,z]
 *     polygons  : list of ([vi0,vi1,vi2,vi3], side_int, color_int)
 *
 * Side int: xp=0 xn=1 yp=2 yn=3 zp=4 zn=5
 *
 * Build:  python setup.py build_ext --inplace
 */

#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

/* ── Compiler portability ────────────────────────────────────────────────── */
#ifdef _MSC_VER
#  include <intrin.h>
   static inline int _ctz32(uint32_t x) {
       unsigned long r; _BitScanForward(&r, x); return (int)r;
   }
#  define builtin_ctz(x) _ctz32(x)
#else
#  define builtin_ctz(x) __builtin_ctz(x)
#endif

/* ── Open-addressing hash map  key=uint64(!=0)  val=uint32 ──────────────── */

typedef struct { uint64_t k; uint32_t v; } HE;

typedef struct { HE *e; uint32_t cap, sz; } HM;

static int hm_init(HM *h, uint32_t cap) {
    uint32_t p=1; while(p<cap) p<<=1; if(p<16) p=16;
    h->cap=p; h->sz=0;
    h->e=(HE*)calloc(p,sizeof(HE));
    return h->e ? 0 : -1;
}
static void hm_free(HM *h)  { free(h->e); h->e=NULL; h->cap=h->sz=0; }
static void hm_clear(HM *h) { memset(h->e,0,h->cap*sizeof(HE)); h->sz=0; }

/* Fibonacci hashing: slot = (key * FIBO) >> (64-log2(cap)) */
#define FIBO 11400714819323198485ULL
static inline uint32_t _slot(uint64_t k, uint32_t cap) {
    /* cap is always power-of-2; builtin_ctz gives log2 */
    int bits = builtin_ctz(cap);
    return (uint32_t)((k * FIBO) >> (64 - bits));
}

static int hm_grow(HM *h) {
    uint32_t nc = h->cap * 2;
    HE *ne = (HE*)calloc(nc, sizeof(HE));
    if (!ne) return -1;
    for (uint32_t i=0; i<h->cap; i++) {
        if (!h->e[i].k) continue;
        uint32_t s = _slot(h->e[i].k, nc);
        while (ne[s & (nc-1)].k) s++;
        ne[s & (nc-1)] = h->e[i];
    }
    free(h->e); h->e=ne; h->cap=nc;
    return 0;
}

/* Returns pointer to val slot (existing or new). NULL on OOM. */
static uint32_t* hm_upsert(HM *h, uint64_t key) {
    if (h->sz * 10 / h->cap >= 6) if (hm_grow(h)<0) return NULL;
    uint32_t mask = h->cap-1;
    uint32_t s    = _slot(key, h->cap);
    while (h->e[s&mask].k && h->e[s&mask].k != key) s++;
    s &= mask;
    if (!h->e[s].k) { h->e[s].k=key; h->sz++; }
    return &h->e[s].v;
}

static uint32_t hm_get(const HM *h, uint64_t key) {
    uint32_t mask=h->cap-1, s=_slot(key,h->cap);
    while (h->e[s&mask].k) {
        if (h->e[s&mask].k==key) return h->e[s&mask].v;
        s++;
    }
    return 0;
}

/* ── Vertex pool ─────────────────────────────────────────────────────────── */

typedef struct { uint16_t x,y,z; } V3;

typedef struct { V3 *v; uint32_t sz,cap; HM map; } VPool;

static int vp_init(VPool *p, uint32_t cap) {
    p->sz=0; p->cap=(cap<64?64:cap);
    p->v=(V3*)malloc(p->cap*sizeof(V3));
    if (!p->v) return -1;
    return hm_init(&p->map, p->cap*2);
}
static void vp_free(VPool *p) { free(p->v); hm_free(&p->map); }

static inline uint64_t vkey(uint16_t x,uint16_t y,uint16_t z) {
    /* shift by 1 to ensure key>=1 (x,y,z in [0,256] -> max = 256<<17|256<<8|256 = fine) */
    return ((uint64_t)(x+1)<<34)|((uint64_t)(y+1)<<17)|(z+1);
}

static uint32_t vp_add(VPool *p, uint16_t x, uint16_t y, uint16_t z) {
    uint64_t  k    = vkey(x,y,z);
    uint32_t *slot = hm_upsert(&p->map, k);
    if (!slot) return 0;
    if (*slot) return *slot;
    if (p->sz>=p->cap) {
        uint32_t nc=p->cap*2;
        V3 *nv=(V3*)realloc(p->v,nc*sizeof(V3));
        if (!nv) return 0;
        p->v=nv; p->cap=nc;
    }
    p->v[p->sz]=(V3){x,y,z};
    return (*slot=++p->sz);
}

/* ── Polygon pool ────────────────────────────────────────────────────────── */

typedef struct { uint32_t vi[4]; uint8_t side,color; } Poly;

typedef struct { Poly *p; uint32_t sz,cap; } PPool;

static int pp_init(PPool *pp, uint32_t cap) {
    pp->sz=0; pp->cap=(cap<64?64:cap);
    pp->p=(Poly*)malloc(pp->cap*sizeof(Poly));
    return pp->p ? 0 : -1;
}
static void pp_free(PPool *pp) { free(pp->p); pp->p=NULL; }

static int pp_push(PPool *pp, const Poly *q) {
    if (pp->sz>=pp->cap) {
        uint32_t nc=pp->cap*2;
        Poly *np=(Poly*)realloc(pp->p,nc*sizeof(Poly));
        if (!np) return -1;
        pp->p=np; pp->cap=nc;
    }
    pp->p[pp->sz++]=*q;
    return 0;
}

/* ── Edge map ────────────────────────────────────────────────────────────── */
/*
 * Two separate hash maps per edge:
 *   em_cnt: edge_key -> count (how many faces share this edge)
 *   em_ref: edge_key -> fref  (fi<<2|ei, stored only when count==1)
 * Avoids packing count+fref into 32 bits (fref needs 22+ bits for 256^3).
 */

static inline uint64_t ekey(uint32_t a, uint32_t b) {
    if (a>b){uint32_t t=a;a=b;b=t;}
    return ((uint64_t)a<<32)|b;
}

/* ── Spatial index ───────────────────────────────────────────────────────── */
/*
 * For axis ax varying, key = (c[other0]<<17)|(c[other1]+1)
 * value: linked list head stored in hash map; nodes in flat array
 */
typedef struct { uint32_t vi; uint16_t coord; uint32_t next; } SN;

typedef struct { HM hm[3]; SN *n; uint32_t nsz,ncap; } SI;

static int si_init(SI *s, uint32_t nv) {
    for(int i=0;i<3;i++) if(hm_init(&s->hm[i],nv+64)<0) return -1;
    s->ncap=nv*3+64; s->nsz=1;
    s->n=(SN*)malloc(s->ncap*sizeof(SN));
    return s->n ? 0 : -1;
}
static void si_free(SI *s){ for(int i=0;i<3;i++) hm_free(&s->hm[i]); free(s->n); }

static inline uint64_t sikey(uint16_t a, uint16_t b) {
    return ((uint64_t)(a+1)<<17)|(b+1);
}

static int si_build(SI *s, const VPool *vp) {
    for(int i=0;i<3;i++) hm_clear(&s->hm[i]);
    s->nsz=1;
    for(uint32_t vi=1;vi<=vp->sz;vi++) {
        const V3 *v=&vp->v[vi-1];
        uint16_t c[3]={v->x,v->y,v->z};
        for(int ax=0;ax<3;ax++) {
            int a0=(ax==0)?1:0, a1=(ax==2)?1:2;
            uint64_t k=sikey(c[a0],c[a1]);
            uint32_t *slot=hm_upsert(&s->hm[ax],k);
            if(!slot) return -1;
            if(s->nsz>=s->ncap) {
                uint32_t nc=s->ncap*2;
                SN *nn=(SN*)realloc(s->n,nc*sizeof(SN));
                if(!nn) return -1;
                s->n=nn; s->ncap=nc;
            }
            uint32_t ni=s->nsz++;
            s->n[ni]=(SN){vi,c[ax],*slot};
            *slot=ni;
        }
    }
    return 0;
}

/* Find T-points on axis-aligned edge (va,vb). Returns count, fills out[]. */
static int si_find(const SI *s, const VPool *vp,
                   uint32_t va, uint32_t vb, uint32_t *out) {
    const V3 *a=&vp->v[va-1], *b=&vp->v[vb-1];
    uint16_t ca[3]={a->x,a->y,a->z}, cb[3]={b->x,b->y,b->z};
    int ax=-1;
    for(int i=0;i<3;i++) if(ca[i]!=cb[i]){ax=i;break;}
    if(ax<0) return 0;
    for(int i=0;i<3;i++) if(i!=ax && ca[i]!=cb[i]) return 0;
    int a0=(ax==0)?1:0, a1=(ax==2)?1:2;
    uint64_t k=sikey(ca[a0],ca[a1]);
    uint32_t ni=hm_get(&s->hm[ax],k);
    uint16_t lo=ca[ax]<cb[ax]?ca[ax]:cb[ax];
    uint16_t hi=ca[ax]>cb[ax]?ca[ax]:cb[ax];
    int cnt=0;
    while(ni){
        const SN *n=&s->n[ni];
        if(n->vi!=va&&n->vi!=vb&&n->coord>lo&&n->coord<hi)
            out[cnt++]=n->vi;
        ni=n->next;
    }
    return cnt;
}

/* ── Sort helpers ────────────────────────────────────────────────────────── */

static int cmp_u16(const void *a, const void *b) {
    return (int)*(const uint16_t*)a - (int)*(const uint16_t*)b;
}

static void uniq_sort(uint16_t *a, int *n) {
    qsort(a,*n,2,cmp_u16);
    int w=0;
    for(int i=0;i<*n;i++) if(!i||a[i]!=a[i-1]) a[w++]=a[i];
    *n=w;
}

static void push_u16(uint16_t *a, int *n, int cap, uint16_t v) {
    if(*n<cap) a[(*n)++]=v;
}

/* ── Core resolver ───────────────────────────────────────────────────────── */

#define MAX_SPLIT 256
#define MAX_TPTS  64

static int resolve_core(VPool *vp, PPool *pp) {
    HM   em_cnt, em_ref;
    SI   si;
    em_cnt.e=NULL; em_ref.e=NULL;
    int  ret=-1;
    uint8_t  *flags=NULL;
    uint32_t *tpts=NULL;
    Poly     *newp=NULL;
    uint32_t  newcap=0;

    if(hm_init(&em_cnt, pp->sz*8+64)<0) return -1;
    if(hm_init(&em_ref, pp->sz*8+64)<0) { hm_free(&em_cnt); return -1; }
    if(si_init(&si, vp->sz+64)<0) { hm_free(&em_cnt); hm_free(&em_ref); return -1; }

    tpts=(uint32_t*)malloc((vp->sz+128)*sizeof(uint32_t));
    if(!tpts) goto done;

    for(int iter=0;iter<60;iter++) {

        /* build edge map */
        hm_clear(&em_cnt); hm_clear(&em_ref);
        for(uint32_t fi=0;fi<pp->sz;fi++) {
            const Poly *q=&pp->p[fi];
            for(int ei=0;ei<4;ei++) {
                uint64_t k=ekey(q->vi[ei],q->vi[(ei+1)%4]);
                uint32_t *sc=hm_upsert(&em_cnt,k);
                if(!sc) goto done;
                if(!*sc) {
                    /* first time: store fref */
                    uint32_t *sr=hm_upsert(&em_ref,k);
                    if(!sr) goto done;
                    *sr = (fi<<2)|ei;
                }
                (*sc)++;
            }
        }

        /* spatial index */
        if(si_build(&si,vp)<0) goto done;

        /* scan open edges for T-junctions */
        free(flags);
        flags=(uint8_t*)calloc(pp->sz,1);
        if(!flags) goto done;

        int any=0;
        for(uint32_t hi=0;hi<em_cnt.cap;hi++) {
            if(!em_cnt.e[hi].k) continue;
            if(em_cnt.e[hi].v != 1) continue;
            uint64_t k=em_cnt.e[hi].k;
            uint32_t fref=hm_get(&em_ref,k);
            uint32_t fi=fref>>2, ei=fref&3;
            uint32_t va=pp->p[fi].vi[ei], vb=pp->p[fi].vi[(ei+1)%4];
            int n=si_find(&si,vp,va,vb,tpts);
            if(n>0){ flags[fi]=1; any=1; }
        }
        if(!any){ ret=0; goto done; }

        /* split */
        if(pp->sz*4+64 > newcap) {
            newcap=pp->sz*4+64;
            Poly *tmp=(Poly*)realloc(newp,newcap*sizeof(Poly));
            if(!tmp) goto done;
            newp=tmp;
        }
        uint32_t ns=0;

        for(uint32_t fi=0;fi<pp->sz;fi++) {
            const Poly *q=&pp->p[fi];
            if(!flags[fi]) {
                if(ns>=newcap){ newcap*=2; Poly*tmp=(Poly*)realloc(newp,newcap*sizeof(Poly)); if(!tmp) goto done; newp=tmp; }
                newp[ns++]=*q;
                continue;
            }

            /* get corner coords */
            uint16_t cx[4],cy[4],cz[4];
            for(int i=0;i<4;i++){
                const V3 *v=&vp->v[q->vi[i]-1];
                cx[i]=v->x; cy[i]=v->y; cz[i]=v->z;
            }

            /* fixed axis */
            int fax=-1;
            if(cx[0]==cx[1]&&cx[1]==cx[2]&&cx[2]==cx[3]) fax=0;
            else if(cy[0]==cy[1]&&cy[1]==cy[2]&&cy[2]==cy[3]) fax=1;
            else if(cz[0]==cz[1]&&cz[1]==cz[2]&&cz[2]==cz[3]) fax=2;
            if(fax<0){ if(ns>=newcap){newcap*=2;Poly*t=(Poly*)realloc(newp,newcap*sizeof(Poly));if(!t)goto done;newp=t;} newp[ns++]=*q; continue; }

            int ax0=(fax==0)?1:0, ax1=(fax==2)?1:2;
            uint16_t *c[3]; c[0]=cx; c[1]=cy; c[2]=cz;
            uint16_t fval=c[fax][0];

            uint16_t uv[2][MAX_SPLIT]; int nu=0,nv=0;
            for(int i=0;i<4;i++){ push_u16(uv[0],&nu,MAX_SPLIT,c[ax0][i]); push_u16(uv[1],&nv,MAX_SPLIT,c[ax1][i]); }

            for(int ei=0;ei<4;ei++){
                uint32_t va=q->vi[ei],vb=q->vi[(ei+1)%4];
                int nt=si_find(&si,vp,va,vb,tpts);
                for(int ti=0;ti<nt;ti++){
                    const V3 *tv=&vp->v[tpts[ti]-1];
                    uint16_t tc[3]={tv->x,tv->y,tv->z};
                    push_u16(uv[0],&nu,MAX_SPLIT,tc[ax0]);
                    push_u16(uv[1],&nv,MAX_SPLIT,tc[ax1]);
                }
            }
            uniq_sort(uv[0],&nu);
            uniq_sort(uv[1],&nv);

            /* winding */
            int32_t area2=0;
            for(int i=0;i<4;i++){
                int ni=(i+1)%4;
                area2+=(int32_t)c[ax0][i]*c[ax1][ni]-(int32_t)c[ax0][ni]*c[ax1][i];
            }
            int ccw=(area2>0);
            uint16_t u0=uv[0][0],u1=uv[0][nu-1],v0=uv[1][0],v1=uv[1][nv-1];

            for(int iu=0;iu<nu-1;iu++) for(int iv=0;iv<nv-1;iv++){
                uint16_t uu0=uv[0][iu],uu1=uv[0][iu+1];
                uint16_t vv0=uv[1][iv],vv1=uv[1][iv+1];
                if(uu0<u0||uu1>u1||vv0<v0||vv1>v1) continue;

                uint16_t pts[4][3];
                #define SP(idx,U,V) pts[idx][fax]=fval; pts[idx][ax0]=(U); pts[idx][ax1]=(V)
                if(ccw){ SP(0,uu0,vv0);SP(1,uu1,vv0);SP(2,uu1,vv1);SP(3,uu0,vv1); }
                else   { SP(0,uu0,vv1);SP(1,uu1,vv1);SP(2,uu1,vv0);SP(3,uu0,vv0); }
                #undef SP

                Poly np; np.side=q->side; np.color=q->color;
                for(int i=0;i<4;i++){
                    np.vi[i]=vp_add(vp,pts[i][0],pts[i][1],pts[i][2]);
                    if(!np.vi[i]) goto done;
                }
                if(ns>=newcap){ newcap*=2; Poly*t=(Poly*)realloc(newp,newcap*sizeof(Poly)); if(!t)goto done; newp=t; }
                newp[ns++]=np;
            }
        }

        /* swap */
        free(pp->p); pp->p=newp; pp->sz=ns; pp->cap=newcap;
        newp=NULL; newcap=0;

        /* grow tpts buffer to match new vertex pool size */
        free(tpts);
        tpts=(uint32_t*)malloc((vp->sz+128)*sizeof(uint32_t));
        if(!tpts) goto done;
    }
    ret=0;

done:
    hm_free(&em_cnt); hm_free(&em_ref); si_free(&si);
    free(flags); free(tpts); free(newp);
    return ret;
}

/* ── Python entry point ──────────────────────────────────────────────────── */


/* Side string -> int: xp=0 xn=1 yp=2 yn=3 zp=4 zn=5 */
static int parse_side(PyObject *o) {
    if (PyLong_Check(o)) return (int)PyLong_AsLong(o);
    if (!PyUnicode_Check(o)) return -1;
    const char *s = PyUnicode_AsUTF8(o);
    if (!s) return -1;
    if (s[0]=='x'&&s[1]=='p') return 0;
    if (s[0]=='x'&&s[1]=='n') return 1;
    if (s[0]=='y'&&s[1]=='p') return 2;
    if (s[0]=='y'&&s[1]=='n') return 3;
    if (s[0]=='z'&&s[1]=='p') return 4;
    if (s[0]=='z'&&s[1]=='n') return 5;
    return -1;
}

/* Side int -> string */
static const char *SIDE_STRS[] = {"xp","xn","yp","yn","zp","zn"};
static PyObject *py_resolve(PyObject *self, PyObject *args) {
    PyObject *rq;
    if(!PyArg_ParseTuple(args,"O",&rq)) return NULL;

    if(!PyList_Check(rq)){ PyErr_SetString(PyExc_TypeError,"expected list"); return NULL; }
    Py_ssize_t n=PyList_GET_SIZE(rq);
    if(!n) {
        PyObject *v=PyList_New(0), *p=PyList_New(0);
        return Py_BuildValue("(NN)",v,p);
    }

    VPool vp; PPool pp;
    if(vp_init(&vp,(uint32_t)(n*2+64))<0||pp_init(&pp,(uint32_t)(n+64))<0){
        vp_free(&vp); pp_free(&pp); return PyErr_NoMemory();
    }

    for(Py_ssize_t i=0;i<n;i++){
        PyObject *item=PyList_GET_ITEM(rq,i);
        if(!PyTuple_Check(item)||PyTuple_GET_SIZE(item)<3){ PyErr_SetString(PyExc_TypeError,"expected (verts,side,color)"); vp_free(&vp);pp_free(&pp);return NULL; }
        PyObject *vl=PyTuple_GET_ITEM(item,0);
        Py_ssize_t vl_sz = PyList_Check(vl) ? PyList_GET_SIZE(vl) :
                           PyTuple_Check(vl) ? PyTuple_GET_SIZE(vl) : -1;
        if(vl_sz!=4){ PyErr_SetString(PyExc_TypeError,"verts must be sequence of 4"); vp_free(&vp);pp_free(&pp);return NULL; }
        #define VL_GET(i) (PyList_Check(vl) ? PyList_GET_ITEM(vl,i) : PyTuple_GET_ITEM(vl,i))
        int side =parse_side(PyTuple_GET_ITEM(item,1));
        int color=(int)PyLong_AsLong(PyTuple_GET_ITEM(item,2));
        if(side<0){ PyErr_SetString(PyExc_ValueError,"invalid side"); vp_free(&vp);pp_free(&pp);return NULL; }
        Poly q; q.side=(uint8_t)side; q.color=(uint8_t)color;
        for(int vi=0;vi<4;vi++){
            PyObject *pt=VL_GET(vi);
            Py_ssize_t pt_sz = PyList_Check(pt) ? PyList_GET_SIZE(pt) :
                               PyTuple_Check(pt) ? PyTuple_GET_SIZE(pt) : -1;
            if(pt_sz<3){ PyErr_SetString(PyExc_TypeError,"each vert must be [x,y,z] or (x,y,z)"); vp_free(&vp);pp_free(&pp);return NULL; }
            #define PT_GET(o,i) (PyList_Check(o) ? PyList_GET_ITEM(o,i) : PyTuple_GET_ITEM(o,i))
            int x=(int)PyLong_AsLong(PT_GET(pt,0));
            int y=(int)PyLong_AsLong(PT_GET(pt,1));
            int z=(int)PyLong_AsLong(PT_GET(pt,2));
            #undef PT_GET
            q.vi[vi]=vp_add(&vp,(uint16_t)x,(uint16_t)y,(uint16_t)z);
            if(!q.vi[vi]){ vp_free(&vp);pp_free(&pp);return PyErr_NoMemory(); }
        }
        #undef VL_GET
        if(pp_push(&pp,&q)<0){ vp_free(&vp);pp_free(&pp);return PyErr_NoMemory(); }
    }

    if(resolve_core(&vp,&pp)<0){ vp_free(&vp);pp_free(&pp);return PyErr_NoMemory(); }

    PyObject *pv=PyList_New(vp.sz);
    if(!pv){ vp_free(&vp);pp_free(&pp);return NULL; }
    for(uint32_t i=0;i<vp.sz;i++){
        const V3 *v=&vp.v[i];
        PyObject *t=Py_BuildValue("(iii)",v->x,v->y,v->z);
        if(!t){ Py_DECREF(pv);vp_free(&vp);pp_free(&pp);return NULL; }
        PyList_SET_ITEM(pv,i,t);
    }

    PyObject *pp2=PyList_New(pp.sz);
    if(!pp2){ Py_DECREF(pv);vp_free(&vp);pp_free(&pp);return NULL; }
    for(uint32_t i=0;i<pp.sz;i++){
        const Poly *q=&pp.p[i];
        PyObject *vis=PyList_New(4);
        if(!vis){ Py_DECREF(pv);Py_DECREF(pp2);vp_free(&vp);pp_free(&pp);return NULL; }
        for(int k=0;k<4;k++) PyList_SET_ITEM(vis,k,PyLong_FromLong(q->vi[k]));
        PyObject *t=Py_BuildValue("(Nsi)",vis,SIDE_STRS[q->side],q->color);
        if(!t){ Py_DECREF(pv);Py_DECREF(pp2);vp_free(&vp);pp_free(&pp);return NULL; }
        PyList_SET_ITEM(pp2,i,t);
    }

    vp_free(&vp); pp_free(&pp);
    return Py_BuildValue("(NN)",pv,pp2);
}

static PyMethodDef methods[]={
    {"resolve_tjunctions",py_resolve,METH_VARARGS,
     "resolve_tjunctions(raw_quads) -> (verts, polys)\n"
     "C T-junction resolver. raw_quads: list of ([[x,y,z]x4], side_int, color_int)"},
    {NULL,NULL,0,NULL}
};

static struct PyModuleDef mod={
    PyModuleDef_HEAD_INIT,"tjunction_resolver",
    "C T-junction resolver for SquaredVoxGameReady.",-1,methods
};

PyMODINIT_FUNC PyInit_tjunction_resolver(void){ return PyModule_Create(&mod); }