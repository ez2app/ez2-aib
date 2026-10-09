/* SPDX-License-Identifier: Apache-2.0 */
/* aib 安全網(10-07):用 LD_PRELOAD 掛在測試的每個行程上,記下 C 層的開檔、列目錄、執行、連線(一行一筆:種類\tpid\t路徑)。
 * 只記路徑,不算雜湊;給 shadow_tracer.native_unrecorded 比對「C 層讀了倉內檔,Python 鉤子卻沒記」⇒ 那支量不到。
 * 由 shadow_tracer.ldp_library() 第一次用到時用 cc 編譯(Linux)。試驗報告:docs/handoff/cloud-1002/SPIKE-ldpreload-1007.md */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <dirent.h>
#include <sys/stat.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>

static __thread int busy = 0;
static int tfd = -2;

static void rec_raw(char kind, const char *p);
/* 相對路徑接上當下的工作目錄(openat 帶資料夾代號的接上那個資料夾)⇒ 一律記絕對路徑(10-07:分析端的工作目錄跟測試不同) */
static void rec_at(char kind, int dirfd, const char *p) {
    if (busy || !p) return;
    if (p[0] == '/') { rec_raw(kind, p); return; }
    char base[4096]; base[0] = 0;
    if (dirfd == AT_FDCWD) {
        if (!getcwd(base, sizeof base)) base[0] = 0;
    } else {
        char link[64]; snprintf(link, sizeof link, "/proc/self/fd/%d", dirfd);
        ssize_t n = readlink(link, base, sizeof base - 1); if (n < 0) n = 0; base[n] = 0;
    }
    if (!base[0]) { rec_raw(kind, p); return; }
    char full[8300]; snprintf(full, sizeof full, "%s/%s", base, p);
    rec_raw(kind, full);
}
static void rec(char kind, const char *p) { rec_at(kind, AT_FDCWD, p); }
static void rec_raw(char kind, const char *p) {
    if (busy || !p) return;
    busy = 1;
    if (tfd == -2) {
        const char *t = getenv("AIB_LDP_TRACE");
        static int (*ro)(const char *, int, ...) = 0;
        if (!ro) ro = dlsym(RTLD_NEXT, "open");
        tfd = t ? ro(t, O_WRONLY | O_APPEND | O_CREAT | O_CLOEXEC, 0644) : -1;
    }
    if (tfd >= 0) {
        char buf[4200];
        int n = snprintf(buf, sizeof buf, "%c\t%d\t%s\n", kind, (int)getpid(), p);
        if (n > 0 && n < (int)sizeof buf) { ssize_t w = write(tfd, buf, n); (void)w; }
    }
    busy = 0;
}

#define WRAP_OPEN(name) \
int name(const char *p, int f, ...) { \
    static int (*real)(const char *, int, ...) = 0; if (!real) real = dlsym(RTLD_NEXT, #name); \
    mode_t m = 0; if (f & (O_CREAT | O_TMPFILE)) { va_list a; va_start(a, f); m = va_arg(a, int); va_end(a); } \
    rec((f & (O_WRONLY | O_RDWR)) ? 'w' : 'o', p); return real(p, f, m); }
WRAP_OPEN(open)
WRAP_OPEN(open64)

#define WRAP_OPENAT(name) \
int name(int d, const char *p, int f, ...) { \
    static int (*real)(int, const char *, int, ...) = 0; if (!real) real = dlsym(RTLD_NEXT, #name); \
    mode_t m = 0; if (f & (O_CREAT | O_TMPFILE)) { va_list a; va_start(a, f); m = va_arg(a, int); va_end(a); } \
    rec_at((f & (O_WRONLY | O_RDWR)) ? 'w' : 'o', d, p); return real(d, p, f, m); }
WRAP_OPENAT(openat)
WRAP_OPENAT(openat64)

FILE *fopen(const char *p, const char *m) { static FILE *(*r)(const char *, const char *) = 0; if (!r) r = dlsym(RTLD_NEXT, "fopen"); rec(strchr(m, 'r') && !strchr(m, '+') ? 'o' : 'w', p); return r(p, m); }
FILE *fopen64(const char *p, const char *m) { static FILE *(*r)(const char *, const char *) = 0; if (!r) r = dlsym(RTLD_NEXT, "fopen64"); rec(strchr(m, 'r') && !strchr(m, '+') ? 'o' : 'w', p); return r(p, m); }
int stat(const char *p, struct stat *s) { static int (*r)(const char *, struct stat *) = 0; if (!r) r = dlsym(RTLD_NEXT, "stat"); rec('s', p); return r(p, s); }
int lstat(const char *p, struct stat *s) { static int (*r)(const char *, struct stat *) = 0; if (!r) r = dlsym(RTLD_NEXT, "lstat"); rec('s', p); return r(p, s); }
int access(const char *p, int m) { static int (*r)(const char *, int) = 0; if (!r) r = dlsym(RTLD_NEXT, "access"); rec('s', p); return r(p, m); }
DIR *opendir(const char *p) { static DIR *(*r)(const char *) = 0; if (!r) r = dlsym(RTLD_NEXT, "opendir"); rec('d', p); return r(p); }
int execve(const char *p, char *const a[], char *const e[]) { static int (*r)(const char *, char *const[], char *const[]) = 0; if (!r) r = dlsym(RTLD_NEXT, "execve"); rec('x', p); return r(p, a, e); }
int connect(int fd, const struct sockaddr *sa, socklen_t l) {
    static int (*r)(int, const struct sockaddr *, socklen_t) = 0; if (!r) r = dlsym(RTLD_NEXT, "connect");
    char b[128] = "?";
    if (sa && sa->sa_family == AF_INET) { const struct sockaddr_in *i = (const void *)sa; snprintf(b, sizeof b, "%s:%d", inet_ntoa(i->sin_addr), ntohs(i->sin_port)); }
    else if (sa && sa->sa_family == AF_UNIX) snprintf(b, sizeof b, "unix");
    else if (sa && sa->sa_family == AF_INET6) snprintf(b, sizeof b, "inet6");
    rec('n', b); return r(fd, sa, l);
}
