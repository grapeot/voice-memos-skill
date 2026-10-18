// Private VoiceMemos API adapter. No UI scripting, SQL writes, or entitlements.
#import <Foundation/Foundation.h>
#import <CoreData/CoreData.h>
#import <objc/message.h>
#import <objc/runtime.h>
#import <dlfcn.h>

static BOOL readOnly = YES;

static BOOL validTitle(NSString *title) {
    // Match Python's Unicode Cc check. Foundation.controlCharacterSet also
    // includes format characters such as the ZWJ used in family emoji.
    NSMutableCharacterSet *controls = [NSMutableCharacterSet characterSetWithRange:NSMakeRange(0, 32)];
    [controls addCharactersInRange:NSMakeRange(0x7f, 33)];
    // Non-Cc whitespace recognized by Python str.strip(); Cc was checked above.
    NSCharacterSet *whitespace = [NSCharacterSet characterSetWithCharactersInString:
        @" \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"];
    return title.length && [title rangeOfCharacterFromSet:controls].location == NSNotFound
        && [title stringByTrimmingCharactersInSet:whitespace].length;
}

static void emit(NSDictionary *payload) {
    NSData *data = [NSJSONSerialization dataWithJSONObject:payload options:0 error:NULL];
    fwrite(data.bytes, 1, data.length, stdout);
    fputc('\n', stdout);
}

static int fail(NSString *code, NSString *message) {
    emit(@{@"error": @{@"code": code, @"message": message ?: @"Native operation failed."}});
    return 6;
}

// The stock container selects an entitlement-restricted XPC store for clients.
// Keep its model/context setup, but use its own local SQLite store description,
// including persistent history tracking and remote-change notifications.
static id localStoreDescription(id self, SEL selector, NSURL *url) {
    NSPersistentStoreDescription *description = ((id (*)(id, SEL, id, BOOL, id))objc_msgSend)(
        NSClassFromString(@"RCPersistentContainer"),
        NSSelectorFromString(@"storeDescriptionForURL:isXPCClient:configuration:"), url, NO, nil);
    description.readOnly = readOnly;
    description.shouldMigrateStoreAutomatically = NO;
    description.shouldInferMappingModelAutomatically = NO;
    return description;
}

static BOOL supports(Class cls, NSString *name, unsigned int arguments, const char *returnType) {
    Method method = class_getInstanceMethod(cls, NSSelectorFromString(name));
    if (!method || method_getNumberOfArguments(method) != arguments) return NO;
    char *type = method_copyReturnType(method);
    BOOL valid = strcmp(type, returnType) == 0;
    free(type);
    return valid;
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc != 5) return fail(@"RENAME_NATIVE_ARGUMENTS", @"Expected database, recording ID, title, and mode.");
        NSString *mode = [NSString stringWithUTF8String:argv[4]];
        if (![@[@"dry-run", @"rename"] containsObject:mode]) return fail(@"RENAME_NATIVE_ARGUMENTS", @"Invalid mode.");
        readOnly = [mode isEqualToString:@"dry-run"];
        NSURL *url = [NSURL fileURLWithPath:[NSString stringWithUTF8String:argv[1]]];
        if (![[NSFileManager defaultManager] fileExistsAtPath:url.path]) return fail(@"DB_NOT_FOUND", @"Database must already exist.");
        NSString *recordingID = [NSString stringWithUTF8String:argv[2]];
        NSString *title = [NSString stringWithUTF8String:argv[3]];
        if (!validTitle(title)) {
            return fail(@"INVALID_TITLE", @"Title must be non-blank and contain no control characters.");
        }
        void *framework = dlopen("/System/iOSSupport/System/Library/PrivateFrameworks/VoiceMemos.framework/VoiceMemos", RTLD_NOW);
        if (!framework) return fail(@"RENAME_UNSUPPORTED", @"Unable to load the private VoiceMemos framework.");
        @try {
            Class containerClass = NSClassFromString(@"RCPersistentContainer");
            Class modelClass = NSClassFromString(@"RCSavedRecordingsModel");
            Class recordingClass = NSClassFromString(@"RCCloudRecording");
            if (!supports(containerClass, @"initWithURL:", 3, "@")
                    || !supports(containerClass, @"storeDescriptionWithURL:", 3, "@")
                    || !supports(object_getClass(containerClass), @"storeDescriptionForURL:isXPCClient:configuration:", 5, "@")
                    || !supports(containerClass, @"newBackgroundModel", 2, "@")
                    || !supports(modelClass, @"performBlockAndWait:", 3, "v")
                    || !supports(modelClass, @"recordingsWithUniqueID:", 3, "@")
                    || !supports(modelClass, @"setTitle:ofRecording:", 4, "v")
                    || !supports(modelClass, @"saveIfNecessary:", 3, @encode(BOOL))
                    || !supports(recordingClass, @"title", 2, "@")
                    || !supports(recordingClass, @"deletionDate", 2, "@")) {
                return fail(@"RENAME_UNSUPPORTED", @"The private VoiceMemos API has changed.");
            }
            Class localClass = objc_allocateClassPair(containerClass, "VMRenameLocalContainer", 0);
            if (!localClass || !class_addMethod(localClass, NSSelectorFromString(@"storeDescriptionWithURL:"),
                    (IMP)localStoreDescription, "@@:@")) {
                return fail(@"RENAME_UNSUPPORTED", @"Unable to configure the native local container.");
            }
            objc_registerClassPair(localClass);
            id container = ((id (*)(id, SEL, id))objc_msgSend)([localClass alloc], NSSelectorFromString(@"initWithURL:"), url);
            if (![container valueForKey:@"store"]) return fail(@"RENAME_STORE_ERROR", @"Unable to open the native store without migration.");
            id model = ((id (*)(id, SEL))objc_msgSend)(container, NSSelectorFromString(@"newBackgroundModel"));
            if (!model) return fail(@"RENAME_STORE_ERROR", @"Unable to obtain a native recordings model.");
            __block NSDictionary *result = nil;
            ((void (*)(id, SEL, id))objc_msgSend)(model, NSSelectorFromString(@"performBlockAndWait:"), ^{
                NSManagedObjectContext *context = [model valueForKey:@"context"];
                context.transactionAuthor = @"voice-memos-skill";
                context.mergePolicy = NSErrorMergePolicy;
                NSArray *matches = ((id (*)(id, SEL, id))objc_msgSend)(model, NSSelectorFromString(@"recordingsWithUniqueID:"), recordingID);
                if (matches.count != 1) {
                    result = @{@"error": @{@"code": @"RENAME_TARGET_NOT_UNIQUE", @"message": @"Expected exactly one recording with the requested ID."}};
                    return;
                }
                id recording = matches.firstObject;
                if ([recording valueForKey:@"deletionDate"]) {
                    result = @{@"error": @{@"code": @"RECORDING_DELETED", @"message": @"Deleted recordings cannot be renamed."}};
                    return;
                }
                NSString *oldTitle = [recording valueForKey:@"title"];
                BOOL unchanged = [oldTitle isEqualToString:title];
                if (!readOnly && !unchanged) {
                    ((void (*)(id, SEL, id, id))objc_msgSend)(model, NSSelectorFromString(@"setTitle:ofRecording:"), title, recording);
                    // setTitle:ofRecording: may save immediately. A subsequent
                    // saveIfNecessary: returns NO when no save is necessary.
                    if (context.hasChanges) {
                        NSError *error = nil;
                        ((BOOL (*)(id, SEL, NSError **))objc_msgSend)(model, NSSelectorFromString(@"saveIfNecessary:"), &error);
                        if (error || context.hasChanges) {
                            [context rollback];
                            result = @{@"error": @{@"code": @"RENAME_SAVE_FAILED", @"message": error.localizedDescription ?: @"Native changes were not saved."}};
                            return;
                        }
                    }
                }
                result = @{@"recording_id": recordingID, @"old_title": oldTitle ?: NSNull.null,
                           @"title": title, @"dry_run": @(readOnly),
                           @"status": unchanged ? @"unchanged" : (readOnly ? @"planned" : @"renamed"),
                           @"verified": @((BOOL)(!readOnly || unchanged))};
            });
            if (!result) return fail(@"RENAME_NATIVE_ERROR", @"The native model returned no result.");
            emit(result);
            return result[@"error"] ? 6 : 0;
        } @catch (NSException *exception) {
            return fail(@"RENAME_NATIVE_ERROR", exception.reason);
        }
    }
}
