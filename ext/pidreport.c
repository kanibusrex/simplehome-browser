/* SimpleHome Browser's web process extension: tells each tab which process it runs in.
 *
 * WebKitGTK has no call that maps a web view to its web process, and the browser needs that to
 * see how much CPU a background tab is using. This runs inside every web process and sends the
 * process ID to the view as a user message, "sh-pid". It does nothing else. */
#include <unistd.h>
#include <webkit2/webkit-web-extension.h>

static void send_pid(WebKitWebPage *page)
{
    WebKitUserMessage *msg = webkit_user_message_new("sh-pid", g_variant_new_int32((gint32)getpid()));
    webkit_web_page_send_message_to_view(page, msg, NULL, NULL, NULL);
}

static void on_document_loaded(WebKitWebPage *page, gpointer data)
{
    send_pid(page);
}

static void on_page_created(WebKitWebExtension *ext, WebKitWebPage *page, gpointer data)
{
    send_pid(page);
    g_signal_connect(page, "document-loaded", G_CALLBACK(on_document_loaded), NULL);
}

G_MODULE_EXPORT void webkit_web_extension_initialize(WebKitWebExtension *ext)
{
    g_signal_connect(ext, "page-created", G_CALLBACK(on_page_created), NULL);
}
