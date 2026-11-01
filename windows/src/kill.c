/* kill.c - list or end processes on Windows 95/98/ME/2000/XP.
 *
 * Windows 9x has no tasklist/taskkill, so a hung program can't be stopped
 * from the remote console without this.
 *
 *   KILL /L               list processes (PID and program)
 *   KILL NAME.EXE         end every process with that program name
 *   KILL C:\DIR\NAME.EXE  end only that program (full paths are known on 9x;
 *                         on NT only the name is, so the name is matched)
 *   KILL 0xPID | PID      end one process by ID (as shown by /L)
 *
 * Uses the Toolhelp32 functions from KERNEL32 (95, 98, ME, 2000, XP),
 * looked up at run time; NT 4 lacks them and gets an error message.
 * Never ends itself. Exit code: 0 if anything was ended (or listed), else 1.
 */
#include "common.h"
#include <tlhelp32.h>

typedef HANDLE (WINAPI *SNAP)(DWORD,DWORD);
typedef BOOL (WINAPI *WALK)(HANDLE,LPPROCESSENTRY32);

static HANDLE g_out;
static void out(const char *s){ DWORD w; WriteFile(g_out,s,slen(s),&w,0); }
static void outhex(DWORD v){
    char b[11]; int i; b[0]='0'; b[1]='x';
    for(i=0;i<8;i++){ int d=(v>>(28-4*i))&15; b[2+i]=(char)(d<10?'0'+d:'A'+d-10); }
    b[10]=0; out(b);
}
static const char *base(const char *p){
    const char *b=p; for(;*p;p++) if(*p=='\\'||*p=='/'||*p==':') b=p+1; return b;
}
static int has_sep(const char *p){ for(;*p;p++) if(*p=='\\'||*p=='/') return 1; return 0; }
/* "0x1A2B" or "1234" -> 1 and *v; else 0 */
static int parse_pid(const char *s, DWORD *v){
    DWORD r=0; int any=0;
    if(s[0]=='0'&&(s[1]=='x'||s[1]=='X')){
        for(s+=2;*s;s++){ int c=lc(*s), d;
            if(c>='0'&&c<='9') d=c-'0'; else if(c>='a'&&c<='f') d=c-'a'+10; else return 0;
            r=r*16+d; any=1; }
    } else {
        for(;*s;s++){ if(*s<'0'||*s>'9') return 0; r=r*10+(*s-'0'); any=1; }
    }
    *v=r; return any;
}

void entry(void){
    char *argv[4]; int argc=get_args(argv,4), list, ended=0, found=0;
    HMODULE k=GetModuleHandle("kernel32.dll");
    SNAP snap=(SNAP)GetProcAddress(k,"CreateToolhelp32Snapshot");
    WALK first=(WALK)GetProcAddress(k,"Process32First");
    WALK next=(WALK)GetProcAddress(k,"Process32Next");
    DWORD self=GetCurrentProcessId(), pid=0; int bypid; const char *want;
    PROCESSENTRY32 pe; HANDLE h;

    g_out=GetStdHandle(STD_OUTPUT_HANDLE);
    if(argc<1||ieq(argv[0],"/?")||ieq(argv[0],"-h")){
        out("KILL /L            list processes\r\n"
            "KILL NAME.EXE      end every process with that name\r\n"
            "KILL C:\\PATH.EXE   end only that program (9x)\r\n"
            "KILL PID           end one process (0x hex or decimal)\r\n");
        ExitProcess(argc<1);
    }
    if(!snap||!first||!next){ out("KILL: process list not available on this Windows version\r\n"); ExitProcess(2); }
    list=ieq(argv[0],"/l"); want=argv[0]; bypid=!list&&parse_pid(want,&pid);

    h=snap(TH32CS_SNAPPROCESS,0);
    if(h==INVALID_HANDLE_VALUE){ out("KILL: cannot list processes\r\n"); ExitProcess(2); }
    pe.dwSize=sizeof(pe);
    if(first(h,&pe)) do {
        int match;
        if(list){ outhex(pe.th32ProcessID); out("  "); out(pe.szExeFile); out("\r\n"); found++; continue; }
        if(pe.th32ProcessID==self) continue;
        if(bypid) match=(pe.th32ProcessID==pid);
        else if(has_sep(want)&&has_sep(pe.szExeFile)) match=ieq(want,pe.szExeFile);
        else match=ieq(base(want),base(pe.szExeFile));
        if(!match) continue;
        found++;
        {   HANDLE p=OpenProcess(PROCESS_TERMINATE,FALSE,pe.th32ProcessID);
            int ok=p&&TerminateProcess(p,1); if(p) CloseHandle(p);
            ended+=ok; out(ok?"ended  ":"FAILED ");
            outhex(pe.th32ProcessID); out("  "); out(pe.szExeFile); out("\r\n"); }
    } while((pe.dwSize=sizeof(pe)), next(h,&pe));
    CloseHandle(h);
    if(!list&&!found) out("KILL: no matching process\r\n");
    ExitProcess(list||ended ? 0 : 1);
}
