/* common.h - freestanding helpers shared by the Windows-side tools.
 *
 * Everything here uses only KERNEL32 exports that exist on Windows 95, 98,
 * ME, NT4, 2000 and XP. No C runtime is linked (-nostdlib): the stock
 * mingw-w64 CRT startup calls APIs missing on the 9x family.
 */
#ifndef RETROGW_COMMON_H
#define RETROGW_COMMON_H
#include <windows.h>

/* gcc may emit calls to these even in freestanding code */
void *memset(void *d,int c,unsigned n){ unsigned char *p=d; while(n--)*p++=(unsigned char)c; return d; }
void *memcpy(void *d,const void *s,unsigned n){ unsigned char *a=d; const unsigned char *b=s; while(n--)*a++=*b++; return d; }
void *memmove(void *d,const void *s,unsigned n){
    unsigned char *a=d; const unsigned char *b=s;
    if(a<b){ while(n--)*a++=*b++; } else { a+=n; b+=n; while(n--)*--a=*--b; }
    return d;
}
/* mingw emits a stack-probe call for large frames; a no-op keeps a
 * freestanding build from jumping into an unresolved stub (GPF on 9x). */
void __chkstk_ms(void){}
void ___chkstk_ms(void){}

static DWORD slen(const char *s){ DWORD n=0; while(s[n]) n++; return n; }
static DWORD atou(const char *s){ DWORD v=0; while(*s>='0'&&*s<='9')v=v*10+(*s++-'0'); return v; }
static int lc(int c){ return (c>='A'&&c<='Z')?c+32:c; }
static int ieq(const char *a,const char *b){
    while(*a&&*b){ if(lc(*a)!=lc(*b)) return 0; a++; b++; }
    return *a==*b;
}
static void putnum(char *b, DWORD *p, DWORD v){
    char t[16]; int n=0; if(!v){ b[(*p)++]='0'; return; }
    while(v){ t[n++]=(char)('0'+(v%10)); v/=10; } while(n) b[(*p)++]=t[--n];
}
static void scat(char *d, DWORD *p, const char *s){ while(*s) d[(*p)++]=*s++; d[*p]=0; }

/* Windows NT/2000/XP vs 95/98/ME: GetVersion() sets the high bit on 9x. */
static int is_nt(void){ return (GetVersion() & 0x80000000UL)==0; }

/* Command interpreter: %COMSPEC%, else command.com (9x) / cmd.exe (NT). */
static void get_comspec(char *out, DWORD cap){
    DWORD n=GetEnvironmentVariable("COMSPEC",out,cap);
    if(n==0||n>=cap){ DWORD p=0; out[0]=0; scat(out,&p,is_nt()?"cmd.exe":"command.com"); }
}

/* Split this process's command line into argv (program name skipped).
 * Handles "quoted args". Modifies a private copy; returns argc. */
static int get_args(char *argv[], int max){
    static char copy[1024]; char *s; int n=0; DWORD i=0; const char *src=GetCommandLine();
    while(src[i]&&i<sizeof(copy)-1){ copy[i]=src[i]; i++; } copy[i]=0;
    s=copy;
    /* skip program name */
    if(*s=='"'){ s++; while(*s&&*s!='"') s++; if(*s) s++; }
    else while(*s&&*s!=' '&&*s!='\t') s++;
    while(*s&&n<max){
        while(*s==' '||*s=='\t') *s++=0;
        if(!*s) break;
        if(*s=='"'){ s++; argv[n++]=s; while(*s&&*s!='"') s++; if(*s) *s++=0; }
        else { argv[n++]=s; while(*s&&*s!=' '&&*s!='\t') s++; }
    }
    return n;
}

/* split a protocol line on spaces (in place) */
static int split(char *s, char *argv[], int max){
    int n=0;
    while(*s && n<max){ while(*s==' ')*s++=0; if(!*s)break; argv[n++]=s; while(*s&&*s!=' ')s++; }
    return n;
}
#endif
