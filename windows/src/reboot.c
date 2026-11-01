/* reboot.c - force a full restart of Windows 95/98/ME/NT4/2000/XP.
 *
 * 9x: ExitWindowsEx works directly. NT: the caller must first enable
 * SeShutdownPrivilege. ADVAPI32's token functions are stubs on 9x, so they
 * are looked up at run time and only called on NT.
 */
#include "common.h"

typedef BOOL (WINAPI *OPT)(HANDLE,DWORD,PHANDLE);
typedef BOOL (WINAPI *LPV)(LPCSTR,LPCSTR,PLUID);
typedef BOOL (WINAPI *ATP)(HANDLE,BOOL,PTOKEN_PRIVILEGES,DWORD,PTOKEN_PRIVILEGES,PDWORD);

static void enable_shutdown_priv(void){
    HMODULE adv=LoadLibrary("advapi32.dll"); HANDLE tok; TOKEN_PRIVILEGES tp;
    OPT open_tok; LPV lookup; ATP adjust;
    if(!adv) return;
    open_tok=(OPT)GetProcAddress(adv,"OpenProcessToken");
    lookup=(LPV)GetProcAddress(adv,"LookupPrivilegeValueA");
    adjust=(ATP)GetProcAddress(adv,"AdjustTokenPrivileges");
    if(!open_tok||!lookup||!adjust) return;
    if(!open_tok(GetCurrentProcess(),TOKEN_ADJUST_PRIVILEGES|TOKEN_QUERY,&tok)) return;
    tp.PrivilegeCount=1; tp.Privileges[0].Attributes=SE_PRIVILEGE_ENABLED;
    if(lookup(0,"SeShutdownPrivilege",&tp.Privileges[0].Luid))
        adjust(tok,FALSE,&tp,0,0,0);
    CloseHandle(tok);
}

void entry(void){
    if(is_nt()) enable_shutdown_priv();
    ExitWindowsEx(EWX_REBOOT|EWX_FORCE, 0);
    ExitProcess(0);
}
