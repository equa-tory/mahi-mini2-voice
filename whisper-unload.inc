    std::string active_model = params.model;
    svr->Get(sparams.request_path + "/model", [&](const Request &, Response &res){
        std::lock_guard<std::mutex> lock(whisper_mutex);
        res.set_content(ctx ? active_model : "", "text/plain");
    });
    svr->Post(sparams.request_path + "/unload", [&](const Request &, Response &res){
        std::lock_guard<std::mutex> lock(whisper_mutex);
        if (ctx) whisper_free(ctx);
        ctx = nullptr;
        state.store(SERVER_STATE_LOADING_MODEL);
        res.set_content("Unloaded", "text/plain");
    });
