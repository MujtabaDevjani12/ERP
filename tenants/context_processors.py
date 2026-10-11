def current_store(request):
    return {"current_store": getattr(request, "store", None)}
