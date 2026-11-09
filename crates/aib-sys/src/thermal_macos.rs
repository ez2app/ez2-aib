//! macOS T1 (IOHID private SPI, looked up with `dlsym`) and T2 (`notify.h`). Every `unsafe`
//! block is one FFI call or one pointer cast with its own SAFETY note.
//!
//! Signatures of the private IOHID functions come from 1.x `aib/tools/heat/rulers.py`
//! (`read_cpu_die_temps`, ctypes) and the IOHIDFamily open-source headers
//! (`IOHIDEventSystemClient.h`, `IOHIDServiceClient.h`, `IOHIDEvent.h`, `IOHIDEventTypes.h`).
//! 1.x declared `IOHIDServiceClientCopyEvent`'s arguments as three `int32`; the headers say
//! `(int64_t type, int32_t options, int64_t timestamp)`, which is what is used here. A wrong
//! signature is undefined behaviour, so the real-machine test in `tests/macos.rs` must pass on
//! Apple Silicon before this is used.

use crate::thermal::{pressure_level, read_die_temps, DieTemps, HidApi, ThermalInfo};
use crate::{Macos, SysError};
use libc::{c_char, c_int, c_void};
use std::ffi::CStr;
use std::sync::OnceLock;

type CFTypeRef = *const c_void;
type CFIndex = isize;
type CFTypeID = usize;

/// `kCFStringEncodingUTF8`.
const UTF8: u32 = 0x0800_0100;
/// `kCFNumberIntType`.
const CF_NUMBER_INT: CFIndex = 9;
/// `kIOHIDEventTypeTemperature`.
const EVENT_TEMPERATURE: i64 = 15;
/// `IOHIDEventFieldBase(kIOHIDEventTypeTemperature)` = `15 << 16`: the temperature level.
const FIELD_TEMPERATURE: u32 = 15 << 16;
/// `PrimaryUsagePage` / `PrimaryUsage` of the AppleSilicon PMU temperature services.
const USAGE_PAGE: c_int = 0xFF00;
const USAGE: c_int = 5;
const IOKIT: &CStr = c"/System/Library/Frameworks/IOKit.framework/IOKit";
/// `kOSThermalNotificationPressureLevelName`.
const PRESSURE_NAME: &CStr = c"com.apple.system.thermalpressurelevel";
/// `NOTIFY_STATUS_OK`.
const NOTIFY_OK: u32 = 0;

/// Opaque CoreFoundation callback tables; only their address is used.
#[repr(C)]
struct CallBacks {
    _p: [u8; 0],
}

// Public CoreFoundation (always present). Only this module, which is macOS-only, links it.
#[link(name = "CoreFoundation", kind = "framework")]
extern "C" {
    static kCFTypeDictionaryKeyCallBacks: CallBacks;
    static kCFTypeDictionaryValueCallBacks: CallBacks;
    fn CFRelease(cf: CFTypeRef);
    fn CFGetTypeID(cf: CFTypeRef) -> CFTypeID;
    fn CFStringGetTypeID() -> CFTypeID;
    fn CFStringCreateWithCString(alloc: CFTypeRef, s: *const c_char, enc: u32) -> CFTypeRef;
    fn CFStringGetCString(s: CFTypeRef, buf: *mut c_char, size: CFIndex, enc: u32) -> u8;
    fn CFNumberCreate(alloc: CFTypeRef, ty: CFIndex, value: *const c_void) -> CFTypeRef;
    fn CFDictionaryCreate(
        alloc: CFTypeRef,
        keys: *const CFTypeRef,
        values: *const CFTypeRef,
        n: CFIndex,
        key_cb: *const CallBacks,
        value_cb: *const CallBacks,
    ) -> CFTypeRef;
    fn CFArrayGetCount(a: CFTypeRef) -> CFIndex;
    fn CFArrayGetValueAtIndex(a: CFTypeRef, i: CFIndex) -> CFTypeRef;
}

// Public libnotify (libSystem, linked by default).
extern "C" {
    fn notify_register_check(name: *const c_char, token: *mut c_int) -> u32;
    fn notify_get_state(token: c_int, state: *mut u64) -> u32;
    fn notify_cancel(token: c_int) -> u32;
}

type ClientCreate = unsafe extern "C" fn(CFTypeRef) -> CFTypeRef;
type ClientSetMatching = unsafe extern "C" fn(CFTypeRef, CFTypeRef);
type ClientCopyServices = unsafe extern "C" fn(CFTypeRef) -> CFTypeRef;
type ServiceCopyProperty = unsafe extern "C" fn(CFTypeRef, CFTypeRef) -> CFTypeRef;
type ServiceCopyEvent = unsafe extern "C" fn(CFTypeRef, i64, i32, i64) -> CFTypeRef;
type EventGetFloat = unsafe extern "C" fn(CFTypeRef, u32) -> f64;

/// The private IOHID functions, found once and kept for the life of the process (the IOKit
/// handle is never `dlclose`d, so these pointers never dangle).
#[derive(Clone, Copy)]
struct Spi {
    client_create: ClientCreate,
    set_matching: ClientSetMatching,
    copy_services: ClientCopyServices,
    copy_property: ServiceCopyProperty,
    copy_event: ServiceCopyEvent,
    get_float: EventGetFloat,
}

/// Looked up on the first T1 call and shared read-only afterwards (function pointers are
/// `Send + Sync`); a failed lookup is remembered too, so it is not retried every round.
static SPI: OnceLock<Result<Spi, SysError>> = OnceLock::new();

/// Look one symbol up in `handle`; null = missing.
fn sym(handle: *mut c_void, name: &CStr) -> Result<*mut c_void, SysError> {
    // SAFETY: `handle` is a live `dlopen` handle that is never closed; `name` is a
    // NUL-terminated C string that outlives the call.
    let p = unsafe { libc::dlsym(handle, name.as_ptr()) };
    if p.is_null() {
        Err(SysError::SpiMissing)
    } else {
        Ok(p)
    }
}

fn load_spi() -> Result<Spi, SysError> {
    load_spi_from(IOKIT)
}

/// [`load_spi`] with the library path passed in, so "symbol not found" is tested on a real
/// library that lacks them.
fn load_spi_from(path: &CStr) -> Result<Spi, SysError> {
    // SAFETY: `path` is a NUL-terminated C string; `dlopen` has no other precondition. The
    // handle is deliberately leaked (never `dlclose`d).
    let handle = unsafe { libc::dlopen(path.as_ptr(), libc::RTLD_LAZY | libc::RTLD_LOCAL) };
    if handle.is_null() {
        return Err(SysError::SpiMissing);
    }
    let p = sym(handle, c"IOHIDEventSystemClientCreate")?;
    // SAFETY: `p` is the address of `IOHIDEventSystemClientCreate`, whose C signature is
    // `IOHIDEventSystemClientRef (CFAllocatorRef)` (IOHIDEventSystemClient.h; 1.x rulers.py).
    let client_create = unsafe { std::mem::transmute::<*mut c_void, ClientCreate>(p) };
    let p = sym(handle, c"IOHIDEventSystemClientSetMatching")?;
    // SAFETY: `void (IOHIDEventSystemClientRef, CFDictionaryRef)` (IOHIDEventSystemClient.h;
    // 1.x ignores any result, so declaring none is the safe reading).
    let set_matching = unsafe { std::mem::transmute::<*mut c_void, ClientSetMatching>(p) };
    let p = sym(handle, c"IOHIDEventSystemClientCopyServices")?;
    // SAFETY: `CFArrayRef (IOHIDEventSystemClientRef)` (IOHIDEventSystemClient.h; rulers.py).
    let copy_services = unsafe { std::mem::transmute::<*mut c_void, ClientCopyServices>(p) };
    let p = sym(handle, c"IOHIDServiceClientCopyProperty")?;
    // SAFETY: `CFTypeRef (IOHIDServiceClientRef, CFStringRef)` (IOHIDServiceClient.h).
    let copy_property = unsafe { std::mem::transmute::<*mut c_void, ServiceCopyProperty>(p) };
    let p = sym(handle, c"IOHIDServiceClientCopyEvent")?;
    // SAFETY: `IOHIDEventRef (IOHIDServiceClientRef, int64_t type, int32_t options, int64_t
    // timestamp)` (IOHIDServiceClient.h).
    let copy_event = unsafe { std::mem::transmute::<*mut c_void, ServiceCopyEvent>(p) };
    let p = sym(handle, c"IOHIDEventGetFloatValue")?;
    // SAFETY: `IOHIDFloat (IOHIDEventRef, IOHIDEventField)`, `IOHIDFloat` = `double` on 64-bit,
    // `IOHIDEventField` = `uint32_t` (IOHIDEvent.h, IOHIDEventTypes.h).
    let get_float = unsafe { std::mem::transmute::<*mut c_void, EventGetFloat>(p) };
    Ok(Spi {
        client_create,
        set_matching,
        copy_services,
        copy_property,
        copy_event,
        get_float,
    })
}

/// Release a Create / Copy result.
fn release(r: CFTypeRef) {
    // SAFETY: callers pass only non-null references they own (Create / Copy rule), once.
    unsafe { CFRelease(r) };
}

/// A Create / Copy result owned by this module's helpers; released on drop.
struct Cf(CFTypeRef);

impl Drop for Cf {
    fn drop(&mut self) {
        release(self.0);
    }
}

fn non_null(r: CFTypeRef) -> Result<CFTypeRef, SysError> {
    if r.is_null() {
        Err(SysError::Unknown)
    } else {
        Ok(r)
    }
}

/// `CFStringCreateWithCString`. **Create** rule.
fn cf_string(s: &CStr) -> Result<Cf, SysError> {
    // SAFETY: NULL allocator = default; `s` is NUL-terminated and outlives the call.
    let r = unsafe { CFStringCreateWithCString(std::ptr::null(), s.as_ptr(), UTF8) };
    non_null(r).map(Cf)
}

/// `CFNumberCreate(kCFNumberIntType)`. **Create** rule.
fn cf_int(v: c_int) -> Result<Cf, SysError> {
    let v: *const c_int = &v;
    // SAFETY: NULL allocator = default; `v` points at a live `int`, which CF copies.
    let r = unsafe { CFNumberCreate(std::ptr::null(), CF_NUMBER_INT, v.cast()) };
    non_null(r).map(Cf)
}

/// Text of a CFString, `None` if `r` is not a CFString or does not fit 256 bytes of UTF-8.
fn string_of(r: CFTypeRef) -> Option<String> {
    // SAFETY: `r` is a live CF object (the caller holds it).
    let ty = unsafe { CFGetTypeID(r) };
    // SAFETY: no arguments, no preconditions.
    let string_ty = unsafe { CFStringGetTypeID() };
    if ty != string_ty {
        return None;
    }
    let mut buf = [0 as c_char; 256];
    // SAFETY: `r` is a live CFString (checked above); `buf` is a writable 256-byte buffer
    // and its size is passed, so CF writes at most that many bytes including the NUL.
    let ok = unsafe { CFStringGetCString(r, buf.as_mut_ptr(), 256, UTF8) };
    if ok == 0 {
        return None;
    }
    // SAFETY: on success CF wrote a NUL-terminated string inside `buf`.
    let s = unsafe { CStr::from_ptr(buf.as_ptr()) };
    s.to_str().ok().map(str::to_owned)
}

/// The real [`HidApi`]: CF objects as raw pointers.
struct RealHid {
    spi: Spi,
    product_key: Cf,
}

impl HidApi for RealHid {
    type Ref = CFTypeRef;

    fn matching(&self) -> Result<CFTypeRef, SysError> {
        let k_page = cf_string(c"PrimaryUsagePage")?;
        let k_usage = cf_string(c"PrimaryUsage")?;
        let v_page = cf_int(USAGE_PAGE)?;
        let v_usage = cf_int(USAGE)?;
        let keys = [k_page.0, k_usage.0];
        let values = [v_page.0, v_usage.0];
        // SAFETY: two live keys and values; the CFType callbacks retain them, so the guards
        // above may release ours afterwards. The callback tables are CF's own statics.
        let r = unsafe {
            CFDictionaryCreate(
                std::ptr::null(),
                keys.as_ptr(),
                values.as_ptr(),
                2,
                &raw const kCFTypeDictionaryKeyCallBacks,
                &raw const kCFTypeDictionaryValueCallBacks,
            )
        };
        non_null(r)
    }

    fn client(&self) -> Result<CFTypeRef, SysError> {
        // SAFETY: NULL allocator = default (signature in `load_spi_from`).
        let r = unsafe { (self.spi.client_create)(std::ptr::null()) };
        non_null(r)
    }

    fn set_matching(&self, client: CFTypeRef, matching: CFTypeRef) {
        // SAFETY: both are live objects we own; the client retains what it keeps.
        unsafe { (self.spi.set_matching)(client, matching) };
    }

    fn services(&self, client: CFTypeRef) -> Option<CFTypeRef> {
        // SAFETY: `client` is a live client we own.
        let r = unsafe { (self.spi.copy_services)(client) };
        (!r.is_null()).then_some(r)
    }

    fn count(&self, array: CFTypeRef) -> usize {
        // SAFETY: `array` is the live CFArray from `services`.
        let n = unsafe { CFArrayGetCount(array) };
        usize::try_from(n).unwrap_or(0)
    }

    fn at(&self, array: CFTypeRef, i: usize) -> Option<CFTypeRef> {
        let i = CFIndex::try_from(i).ok()?;
        // SAFETY: `array` is live and `i < count(array)` (the walk's loop bound).
        let r = unsafe { CFArrayGetValueAtIndex(array, i) };
        (!r.is_null()).then_some(r)
    }

    fn product(&self, service: CFTypeRef) -> Option<(CFTypeRef, Option<String>)> {
        // SAFETY: `service` is borrowed from the live services array; the key is our live
        // CFString.
        let r = unsafe { (self.spi.copy_property)(service, self.product_key.0) };
        if r.is_null() {
            return None;
        }
        Some((r, string_of(r)))
    }

    fn event(&self, service: CFTypeRef) -> Option<CFTypeRef> {
        // SAFETY: `service` is borrowed from the live services array; options 0, timestamp 0
        // as in 1.x.
        let r = unsafe { (self.spi.copy_event)(service, EVENT_TEMPERATURE, 0, 0) };
        (!r.is_null()).then_some(r)
    }

    fn celsius(&self, event: CFTypeRef) -> f64 {
        // SAFETY: `event` is a live temperature event we own.
        unsafe { (self.spi.get_float)(event, FIELD_TEMPERATURE) }
    }

    fn release(&self, r: CFTypeRef) {
        release(r);
    }
}

fn die_temps_with(spi: &Result<Spi, SysError>) -> Result<DieTemps, SysError> {
    let spi = spi.clone()?;
    let hid = RealHid {
        spi,
        product_key: cf_string(c"Product")?,
    };
    read_die_temps(&hid)
}

/// A registered notify token; cancelled on drop.
struct Token(c_int);

impl Drop for Token {
    fn drop(&mut self) {
        // SAFETY: `self.0` is a token `notify_register_check` gave us, cancelled once.
        unsafe { notify_cancel(self.0) };
    }
}

fn pressure_raw() -> Result<u64, SysError> {
    let mut token: c_int = 0;
    // SAFETY: the name is NUL-terminated and static; `token` is a live out-parameter.
    let st = unsafe { notify_register_check(PRESSURE_NAME.as_ptr(), &mut token) };
    if st != NOTIFY_OK {
        return Err(SysError::Os(i32::try_from(st).unwrap_or(-1)));
    }
    let token = Token(token);
    let mut state: u64 = 0;
    // SAFETY: `token.0` is a live registration; `state` is a live out-parameter.
    let st = unsafe { notify_get_state(token.0, &mut state) };
    if st != NOTIFY_OK {
        return Err(SysError::Os(i32::try_from(st).unwrap_or(-1)));
    }
    Ok(state)
}

impl ThermalInfo for Macos {
    fn die_temps(&self) -> Result<DieTemps, SysError> {
        die_temps_with(SPI.get_or_init(load_spi))
    }

    fn pressure(&self) -> Result<i64, SysError> {
        pressure_level(pressure_raw()?)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn missing_spi_symbols_are_spi_missing_not_a_crash() {
        // libSystem has none of the IOHID symbols.
        let r = load_spi_from(c"/usr/lib/libSystem.B.dylib");
        assert_eq!(r.err(), Some(SysError::SpiMissing));
        assert_eq!(
            die_temps_with(&Err(SysError::SpiMissing)),
            Err(SysError::SpiMissing)
        );
        assert_eq!(SysError::SpiMissing.tag(), "spi_missing");
    }

    #[test]
    fn missing_library_is_spi_missing() {
        let r = load_spi_from(c"/System/Library/Frameworks/NoSuch.framework/NoSuch");
        assert_eq!(r.err(), Some(SysError::SpiMissing));
    }
}
