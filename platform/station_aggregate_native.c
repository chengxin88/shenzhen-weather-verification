/* Bounded scatter sums, independent of the SQLite and Python ABIs.
 * SQLite >=3.42 uses Neumaier SUM. Counters are checked uint32_t values.
 * Sparse layouts omit moments/counters which cannot affect a product.
 */
#include <math.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#ifdef __GLIBC__
#include <malloc.h>
#endif

#define BLOCK 256
#define EXTRAS 23

typedef struct { unsigned char *data; int type; } Group;
typedef struct {
    int columns, windows, compensated, thresholds;
    size_t allocated, limit, blocks;
    Group **data;
} Accumulator;

int agg_abi(void) { return 2; }

static int counters(Accumulator *a, int type) {
    if (type==0) return 3; /* n, N1, N2 */
    if (type==1) return 1; /* n */
    return 1+a->thresholds+(type==2 ? 1 : EXTRAS-2);
}
static int moments(Accumulator *a, int type) { return a->windows==1 || type<2; }
static size_t stride(Accumulator *a, int type) {
    size_t n=(moments(a,type) ? 16*sizeof(double) : 0)+counters(a,type)*sizeof(uint32_t);
    return (n+7)&~(size_t)7;
}

void *agg_new(int columns, int windows, int compensated, size_t limit) {
    Accumulator *a=calloc(1,sizeof(*a));
    if (!a) return NULL;
    a->columns=columns; a->windows=windows; a->compensated=compensated;
    a->thresholds=columns-9-EXTRAS; a->limit=limit;
    return a;
}

static Group *group(Accumulator *a, int64_t id, int type) {
    size_t block=(size_t)id/BLOCK;
    if (block>=a->blocks) {
        size_t blocks=block+1;
        Group **p=realloc(a->data,blocks*sizeof(Group *));
        if (!p) return NULL;
        a->data=p;
        while (a->blocks<blocks) {
            a->data[a->blocks]=calloc(BLOCK,sizeof(Group));
            if (!a->data[a->blocks]) return NULL;
            a->blocks++;
        }
    }
    Group *g=&a->data[block][(size_t)id%BLOCK];
    if (!g->data) {
        size_t size=stride(a,type)*a->windows;
        if (a->allocated+size>a->limit) return NULL;
        g->data=calloc(1,size);
        if (!g->data) return NULL;
        g->type=type; a->allocated+=size;
    }
    return g;
}

/* Counter layout -> original moment/threshold/attachment-7 column. */
static int column(Accumulator *a, int type, int c) {
    if (!c) return 0;
    int extras=9+a->thresholds;
    if (type==0) return extras+c-1;
    if (c<=a->thresholds) return 8+c;
    return extras+2+(c-a->thresholds-1);
}

int agg_add(void *handle, int64_t rows, int streams, const int64_t *ids,
            const uint8_t *masks, const double *values, const uint8_t *types) {
    Accumulator *a=handle;
    for (int64_t r=0;r<rows;r++) {
        const double *v=values+r*a->columns; int type=types[r];
        for (int s=0;s<streams;s++) {
            int64_t id=ids[r*streams+s];
            if (id<0 || !masks[r]) continue;
            Group *g=group(a,id,type);
            if (!g) return -1;
            for (int w=0;w<a->windows;w++) {
                if (!(masks[r] & (1<<w))) continue;
                unsigned char *base=g->data+w*stride(a,type);
                if (moments(a,type)) {
                    double *sum=(double *)base, *error=sum+8;
                    for (int c=0;c<8;c++) {
                        double x=v[c+1];
                        if (a->compensated) {
                            double old=sum[c], t=old+x;
                            error[c]+=fabs(old)>fabs(x) ? (old-t)+x : (x-t)+old;
                            sum[c]=t;
                        } else sum[c]+=x;
                    }
                    base+=16*sizeof(double);
                }
                uint32_t *counts=(uint32_t *)base;
                for (int c=0;c<counters(a,type);c++) {
                    uint32_t x=(uint32_t)v[column(a,type,c)];
                    if (UINT32_MAX-counts[c]<x) return -1;
                    counts[c]+=x;
                }
            }
        }
    }
    return 0;
}

int agg_get(void *handle, int64_t id, int window, double *out) {
    Accumulator *a=handle;
    if ((size_t)id/BLOCK>=a->blocks) return 0;
    Group *g=&a->data[(size_t)id/BLOCK][(size_t)id%BLOCK];
    if (!g->data) return 0;
    memset(out,0,a->columns*sizeof(double));
    unsigned char *base=g->data+window*stride(a,g->type);
    if (moments(a,g->type)) {
        double *sum=(double *)base;
        for (int c=0;c<8;c++) out[c+1]=sum[c]+sum[c+8];
        base+=16*sizeof(double);
    }
    uint32_t *counts=(uint32_t *)base;
    for (int c=0;c<counters(a,g->type);c++) out[column(a,g->type,c)]=counts[c];
    return out[0]>0;
}

void agg_free(void *handle) {
    Accumulator *a=handle;
    if (!a) return;
    for (size_t b=0;b<a->blocks;b++) {
        for (int i=0;i<BLOCK;i++) free(a->data[b][i].data);
        free(a->data[b]);
    }
    free(a->data); free(a);
#ifdef __GLIBC__
    malloc_trim(0);
#endif
}

/* Private persisted states: header {type,moments,threshold counters,version},
 * compensated unrounded moments, 64-bit n and checked signed 32-bit extras. No public schema.
 * Compensations must survive day/partition merges, especially for pressure CC.
 */
#include <sqlite3ext.h>
SQLITE_EXTENSION_INIT1
#define STATE_MAX 1024
static int state_counts(const unsigned char *p) {
    int t=p[0]; return t==0 ? 3 : t==1 ? 1 : 1+p[2]+(t==2 ? 1 : EXTRAS-2);
}
static int state_size(const unsigned char *p) {
    return 8+(p[1] ? 128 : 0)+8+4*(state_counts(p)-1);
}
int agg_pack(void *handle, int64_t id, int window, int compact, void *out) {
    Accumulator *a=handle;
    if ((size_t)id/BLOCK>=a->blocks) return 0;
    Group *g=&a->data[(size_t)id/BLOCK][(size_t)id%BLOCK];
    if (!g->data) return 0;
    unsigned char *p=out; memset(p,0,STATE_MAX);
    p[0]=g->type; p[1]=moments(a,g->type) && !(compact && g->type>=2);
    p[2]=a->thresholds; p[3]=2;
    unsigned char *base=g->data+window*stride(a,g->type);
    if (p[1]) memcpy(p+8,base,128);
    if (moments(a,g->type)) base+=128;
    unsigned char *counts=p+8+(p[1] ? 128 : 0);
    int64_t n=((uint32_t *)base)[0];memcpy(counts,&n,8);
    for (int c=1;c<state_counts(p);c++) {int32_t v=((uint32_t *)base)[c];memcpy(counts+8+4*(c-1),&v,4);}
    return state_size(p);
}
static void dd_add(double *sum, double *err, double x) {
    double s=*sum+x, e=fabs(*sum)>fabs(x) ? (*sum-s)+x : (x-s)+*sum;
    /* Renormalize so repeated rolling subtraction does not lose low bits. */
    double t=*err+e, u=s+t;
    *err=(s-u)+t; *sum=u;
}
static int state_merge(unsigned char *a,const unsigned char *b,int sign) {
    if (memcmp(a,b,8)) return 0;
    if (a[1]) {
        double *x=(double *)(a+8); const double *y=(const double *)(b+8);
        for (int c=0;c<8;c++) { dd_add(x+c,x+c+8,sign*y[c]); dd_add(x+c,x+c+8,sign*y[c+8]); }
    }
    unsigned char *x=a+8+(a[1] ? 128 : 0);
    const unsigned char *y=b+8+(b[1] ? 128 : 0);
    int64_t nx,ny;memcpy(&nx,x,8);memcpy(&ny,y,8);nx+=sign*ny;memcpy(x,&nx,8);
    for (int c=1;c<state_counts(a);c++) {
        int32_t vx,vy;memcpy(&vx,x+8+4*(c-1),4);memcpy(&vy,y+8+4*(c-1),4);
        int64_t v=(int64_t)vx+sign*(int64_t)vy;
        if (v>INT32_MAX || v<INT32_MIN) return 0;
        vx=(int32_t)v;memcpy(x+8+4*(c-1),&vx,4);
    }
    return 1;
}
int agg_decode_checked(const void *data,int bytes,int columns,double *out) {
    const unsigned char *p=data;
    if (bytes<16 || p[2]!=columns-9-EXTRAS || p[0]>3 || p[1]>1 || p[2]>100 || p[3]!=2 || bytes!=state_size(p)) return -1;
    Accumulator a={0};a.thresholds=p[2]; a.columns=9+p[2]+EXTRAS;
    memset(out,0,a.columns*sizeof(double));
    if (p[1]) { const double *x=(const double *)(p+8); for (int c=0;c<8;c++) out[c+1]=x[c]+x[c+8]; }
    const unsigned char *counts=p+8+(p[1] ? 128 : 0);
    int64_t n;memcpy(&n,counts,8);out[0]=(double)n;
    for (int c=1;c<state_counts(p);c++) {int32_t v;memcpy(&v,counts+8+4*(c-1),4);out[column(&a,p[0],c)]=v;}
    return n>0;
}
static const unsigned char *valid_state(sqlite3_context *ctx,sqlite3_value *v) {
    const unsigned char *p=sqlite3_value_blob(v); int n=sqlite3_value_bytes(v);
    if (!p || n<16 || p[0]>3 || p[1]>1 || p[2]>100 || p[3]!=2 || n!=state_size(p)) {
        sqlite3_result_error(ctx,"invalid aggregate state",-1);return NULL;
    }
    return p;
}
static void sql_merge(sqlite3_context *ctx,int argc,sqlite3_value **argv) {
    (void)argc;
    const unsigned char *a=valid_state(ctx,argv[0]),*b=valid_state(ctx,argv[1]);
    if (!a || !b) return;
    unsigned char out[STATE_MAX];memcpy(out,a,state_size(a));
    if (!state_merge(out,b,sqlite3_value_int(argv[2]))) {sqlite3_result_error(ctx,"aggregate layout mismatch",-1);return;}
    sqlite3_result_blob(ctx,out,state_size(out),SQLITE_TRANSIENT);
}
static void sql_step(sqlite3_context *ctx,int argc,sqlite3_value **argv) {
    (void)argc;const unsigned char *p=valid_state(ctx,argv[0]);if (!p) return;
    unsigned char *out=sqlite3_aggregate_context(ctx,STATE_MAX);if (!out) {sqlite3_result_error_nomem(ctx);return;}
    if (!out[3]) memcpy(out,p,state_size(p));
    else if (!state_merge(out,p,1)) sqlite3_result_error(ctx,"aggregate layout mismatch",-1);
}
static void sql_final(sqlite3_context *ctx) {
    unsigned char *p=sqlite3_aggregate_context(ctx,0);
    if (p && p[3]) sqlite3_result_blob(ctx,p,state_size(p),SQLITE_TRANSIENT);
}
static void sql_count(sqlite3_context *ctx,int argc,sqlite3_value **argv) {
    (void)argc;const unsigned char *p=valid_state(ctx,argv[0]);
    if (p) {int64_t n;memcpy(&n,p+8+(p[1] ? 128 : 0),8);sqlite3_result_int64(ctx,n);}
}
static void sql_scale(sqlite3_context *ctx,int argc,sqlite3_value **argv) {
    (void)argc;const unsigned char *p=valid_state(ctx,argv[0]);if (!p) return;
    unsigned char out[STATE_MAX];memcpy(out,p,state_size(p));
    int sign=sqlite3_value_int(argv[1]);
    if (out[1]) {double *x=(double *)(out+8);for (int c=0;c<16;c++) x[c]*=sign;}
    unsigned char *x=out+8+(out[1] ? 128 : 0);
    int64_t n;memcpy(&n,x,8);n*=sign;memcpy(x,&n,8);
    for (int c=1;c<state_counts(out);c++) {int32_t v;memcpy(&v,x+8+4*(c-1),4);v*=sign;memcpy(x+8+4*(c-1),&v,4);}
    sqlite3_result_blob(ctx,out,state_size(out),SQLITE_TRANSIENT);
}
static void sql_detail(sqlite3_context *ctx,int argc,sqlite3_value **argv) {
    (void)argc;const unsigned char *p=valid_state(ctx,argv[0]);if (!p) return;
    unsigned char out[STATE_MAX];memcpy(out,p,state_size(p));
    if (p[0]>=2 && p[1]) {
        out[1]=0;memcpy(out+8,p+136,state_size(p)-136);
    }
    sqlite3_result_blob(ctx,out,state_size(out),SQLITE_TRANSIENT);
}
int sqlite3_extension_init(sqlite3 *db,char **err,const sqlite3_api_routines *api) {
    (void)err;SQLITE_EXTENSION_INIT2(api);
    int rc=sqlite3_create_function(db,"agg_merge",3,SQLITE_UTF8|SQLITE_DETERMINISTIC,NULL,sql_merge,NULL,NULL);
    if (rc==SQLITE_OK) rc=sqlite3_create_function(db,"agg_scale",2,SQLITE_UTF8|SQLITE_DETERMINISTIC,NULL,sql_scale,NULL,NULL);
    if (rc==SQLITE_OK) rc=sqlite3_create_function(db,"agg_total",1,SQLITE_UTF8,NULL,NULL,sql_step,sql_final);
    if (rc==SQLITE_OK) rc=sqlite3_create_function(db,"agg_count",1,SQLITE_UTF8|SQLITE_DETERMINISTIC,NULL,sql_count,NULL,NULL);
    if (rc==SQLITE_OK) rc=sqlite3_create_function(db,"agg_detail",1,SQLITE_UTF8|SQLITE_DETERMINISTIC,NULL,sql_detail,NULL,NULL);
    return rc;
}
