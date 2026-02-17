#include <amxmodx>
#include <file>
#include <ultrahc_chat_manager>
#include <easy_http>
#include <sqlx>

#tryinclude <map_manager>

#define PLUGIN_NAME 		"ULTRAHC Discord hooks"
#define PLUGIN_VERSION 	"0.3-dbg-20260217-11"
#define PLUGIN_AUTHOR 	"Asura"

//-----------------------------------------

#define PLUGIN_CFG_NAME "ultrahc_discord" // cfg name
#define DISCORD_PREFIX "[^4Discord^1]"

#define MESSAGEMODE_NAME "adminchat"

//-----------------------------------------

#define TEXT_LENGHT 128
#define DS_SEND_CMD_TEXT_LENGTH 256
#define DS_SEND_AUTHOR_LENGTH 64
#define DS_SEND_MESSAGE_LENGTH 192
#define WEBHOOK_TYPE_CODE_INFO 1
#define WEBHOOK_TYPE_CODE_MESSAGE 2
#define INFO_WEBHOOK_ENABLED 1
#define INFO_JSON_LENGTH 4096
#define INFO_PUSH_DEBOUNCE_SEC 1.0
#define INFO_PUSH_HEARTBEAT_SEC 30.0
#define INFO_MIN_POST_INTERVAL_SEC 0.8
#define INFO_INCLUDE_PLAYERS 0
#define DS_PREFIX_LOOKUP_ENABLED 0
#define TASK_INFO_PUSH 60001
#define TASK_INFO_HEARTBEAT 60002
#define MAP_QUERY_MODE_LENGTH 32
#define MAP_ENTRY_LENGTH 128

#define MAPS_OUTPUT_BEGIN "ULTRAHC_MAPS_BEGIN"
#define MAPS_OUTPUT_END "ULTRAHC_MAPS_END"
#define MAPS_OUTPUT_ERROR "ULTRAHC_MAPS_ERROR"

new const MAP_ROTATION_FILE[] = "maps_ultrahc.ini";

#define CVARS_LENGTH 128

enum ECvarsList {
	_webhook_token,
	_webhook_url,
	
	_sql_host,
	_sql_user,
	_sql_pass,
	_sql_db
}

// U can change it. But be carefully
new const __saytext_teams[][] = {
	"", // All chat
	"(DEAD)", // All chat, but sender is dead
	"(T)", 
	"(DEAD)(T)",
	"(CT)",
	"(DEAD)(CT)",
	"(S)", // Spec team
	"(SPEC)" // All chat, but sender in spec team
}

new __cvar_str_list[ECvarsList][CVARS_LENGTH];

new Handle:__sql_handle;
new bool:g_info_request_in_flight = false;
new Float:g_info_last_send_time = 0.0;
new g_info_skip_inflight = 0;
new g_info_skip_rate = 0;

// new big_string[5000];

public plugin_init() {
	register_plugin(PLUGIN_NAME, PLUGIN_VERSION, PLUGIN_AUTHOR);
	
	register_clcmd("say", "SayMessageHandler");
	register_clcmd("say_team", "SayMessageHandler");
	
	register_clcmd(MESSAGEMODE_NAME, "MessageModeCallback");
	
	// Default Discord->CS path uses legacy-stable formatter (client_print_color)
	register_srvcmd("ultrahc_ds_send_msg", "HookMsgFromDsDefault");
	register_concmd("ultrahc_ds_send_msg", "HookMsgFromDsDefault");
	register_clcmd("ultrahc_ds_send_msg", "HookMsgFromDsDefault", ADMIN_RCON);
	register_srvcmd("ultrahc_ds_send_msg_dbg", "HookMsgFromDsDefault");
	register_concmd("ultrahc_ds_send_msg_dbg", "HookMsgFromDsDefault");
	register_clcmd("ultrahc_ds_send_msg_dbg", "HookMsgFromDsDefault", ADMIN_RCON);
	// Keep the newer variant under explicit test command
	register_srvcmd("ultrahc_ds_send_msg_new", "HookMsgFromDs");
	register_concmd("ultrahc_ds_send_msg_new", "HookMsgFromDs");
	register_clcmd("ultrahc_ds_send_msg_new", "HookMsgFromDs", ADMIN_RCON);
	register_srvcmd("ultrahc_ds_send_msg_old", "HookMsgFromDsOld");
	register_concmd("ultrahc_ds_send_msg_old", "HookMsgFromDsOld");
	register_clcmd("ultrahc_ds_send_msg_old", "HookMsgFromDsOld", ADMIN_RCON);
	register_srvcmd("ultrahc_ds_diag", "HookDiagCmd");
	register_concmd("ultrahc_ds_diag", "HookDiagCmd");
	register_clcmd("ultrahc_ds_diag", "HookDiagCmd", ADMIN_RCON);
	register_srvcmd("ultrahc_ds_dbg_players", "HookDbgPlayersCmd");
	register_concmd("ultrahc_ds_dbg_players", "HookDbgPlayersCmd");
	register_clcmd("ultrahc_ds_dbg_players", "HookDbgPlayersCmd", ADMIN_RCON);
	register_concmd("ultrahc_ds_change_map", "HookChangeMapCmd");
	register_concmd("ultrahc_ds_kick_player", "HookKickPlayerCmd");
	register_concmd("ultrahc_ds_get_maps", "HookGetMapsCmd");
	
	register_concmd("ultrahc_ds_get_info", "HookGetinfoCmd");
	#if INFO_WEBHOOK_ENABLED
		register_event("DeathMsg", "OnDeathMsg", "a");
		register_event("TeamInfo", "OnTeamInfo", "a");
	#endif
	
	#if defined _map_manager_core_included
		register_concmd("ultrahc_ds_reload_map_list", "HookReloadMapList");
		mapm_block_load_maplist();
	#endif
	
	bind_pcvar_string(create_cvar("ultrahc_ds_webhook_token", ""), __cvar_str_list[_webhook_token], CVARS_LENGTH);
	bind_pcvar_string(create_cvar("ultrahc_ds_webhook_url", ""), __cvar_str_list[_webhook_url], CVARS_LENGTH);
	
	bind_pcvar_string(create_cvar("ultrahc_ds_sql_host", ""), __cvar_str_list[_sql_host], CVARS_LENGTH);
	bind_pcvar_string(create_cvar("ultrahc_ds_sql_user", ""), __cvar_str_list[_sql_user], CVARS_LENGTH);
	bind_pcvar_string(create_cvar("ultrahc_ds_sql_pass", ""), __cvar_str_list[_sql_pass], CVARS_LENGTH);
	bind_pcvar_string(create_cvar("ultrahc_ds_sql_db", ""), __cvar_str_list[_sql_db], CVARS_LENGTH);

	#if INFO_WEBHOOK_ENABLED
		set_task(INFO_PUSH_HEARTBEAT_SEC, "InfoHeartbeatTask", TASK_INFO_HEARTBEAT, "", 0, "b");
		ScheduleInfoPush(5.0);
	#endif
	
	AutoExecConfig(true, PLUGIN_CFG_NAME);
	server_print("[ultrahc_discord] loaded build=%s", PLUGIN_VERSION);
}

//-----------------------------------------

public OnConfigsExecuted() {
	__sql_handle = SQL_MakeDbTuple(__cvar_str_list[_sql_host], __cvar_str_list[_sql_user], __cvar_str_list[_sql_pass], __cvar_str_list[_sql_db]);
	SQL_SetCharset(__sql_handle, "utf8");
}

//-----------------------------------------

public client_putinserver(client_id) {
	if(is_user_bot(client_id) || is_user_hltv(client_id)) return;

	#if INFO_WEBHOOK_ENABLED
		ScheduleInfoPush();
	#endif
	#if DS_PREFIX_LOOKUP_ENABLED
		set_task(1.0, "ClientPutInhandler", client_id);
	#endif
}

public client_disconnected(client_id) {
	if(is_user_hltv(client_id)) return;

	#if INFO_WEBHOOK_ENABLED
		ScheduleInfoPush();
	#endif
}

//-----------------------------------------
public ClientPutInhandler(client_id) {
	#if !DS_PREFIX_LOOKUP_ENABLED
		return;
	#endif

	if(!ultrahc_is_pref_file_load()) {
		set_task(1.0, "ClientPutInhandler", client_id);
		return;
	}
	set_task(2.0, "GetMeTime", client_id);	
}
	
public GetMeTime(client_id) {
	#if !DS_PREFIX_LOOKUP_ENABLED
		return;
	#endif

	if(!is_user_connected(client_id) || is_user_hltv(client_id) || is_user_bot(client_id)) return;

	new steam_id[32];
	get_user_authid(client_id, steam_id, charsmax(steam_id));
	
	new sql_request[512];
	formatex(sql_request, charsmax(sql_request), "SELECT ds_display_name FROM users WHERE steam_id = '%s'", steam_id);
	
	new data[8];
	num_to_str(client_id, data, charsmax(data));
	
	SQL_ThreadQuery(__sql_handle, "SQLHandler", sql_request, data, charsmax(data));
}

public SQLHandler(failstate, Handle:query, error[], errnum, data[], size, queuetime) {
	#if !DS_PREFIX_LOOKUP_ENABLED
		return;
	#endif

	// failstate:
	// #define TQUERY_CONNECT_FAILED -2
	// #define TQUERY_QUERY_FAILED -1
	// #define TQUERY_SUCCESS 0
	if(failstate == TQUERY_CONNECT_FAILED) {
		server_print("===============================");
		server_print("	ultrahc_discord: SQL CONNECTION FAILED", failstate);
		server_print("	%s", error);
		server_print("===============================");
		return;
	}
	else if(failstate == TQUERY_QUERY_FAILED) {
		server_print("===============================");
		server_print("	ultrahc_discord: SQL QUERY FAILED", failstate);
		server_print("	%s", error);
		server_print("===============================");
		return;
	}

	if(SQL_NumResults(query) == 0) return;
	
	new username[64];
	SQL_ReadResult(query, 0, username, charsmax(username));
	
	new client_id = str_to_num(data);

	ultrahc_add_prefix(client_id, username, 4);
}

//-----------------------------------------

public SayMessageHandler(owner_id) {
	new con_cmd_text[TEXT_LENGHT];

	// read a command. In this context it will be "say" or "say_team"
	read_argv(0, con_cmd_text, charsmax(con_cmd_text));
	new is_say_team = (con_cmd_text[3] == '_'); // "say_team"[3] = "_"
	
	// read an argument
	read_args(con_cmd_text, charsmax(con_cmd_text));
	remove_quotes(con_cmd_text);
	trim(con_cmd_text);
	
	if(con_cmd_text[0] == '/') {
		
		new match = contain(con_cmd_text, "/notify");
		if(match == 0) SetMsgModeNotify(owner_id);
	
		return PLUGIN_CONTINUE;
	}
	if(con_cmd_text[0] == '@') return PLUGIN_CONTINUE; // admin chat
	if(equali(con_cmd_text, "")) return PLUGIN_CONTINUE; // empty string
	
	new is_owner_alive = is_user_alive(owner_id);
	new owner_team = get_user_team(owner_id);
	new channel_in_use = GetChannel(is_say_team, is_owner_alive, owner_team);
	
	new owner_name[MAX_NAME_LENGTH];
	get_user_name(owner_id, owner_name, charsmax(owner_name));


	// send to discord webhook
	new EzHttpOptions:options_id = ezhttp_create_options()
	
	ezhttp_option_set_header(options_id, "Authorization", __cvar_str_list[_webhook_token])
	ezhttp_option_set_header(options_id, "Content-Type", "application/json")
  
	new json[1024];
	new json_len = 0;
  
	json_len += formatex(json[json_len], charsmax(json) - json_len, "{");
	
	replace_all(owner_name, charsmax(owner_name), "\\", "\\\\");
	replace_all(owner_name, charsmax(owner_name), "^"", "'");
	replace_all(con_cmd_text, charsmax(con_cmd_text), "\\", "\\\\");
	replace_all(con_cmd_text, charsmax(con_cmd_text), "^"", "'");
	
	new steam_id[64];
	get_user_authid(owner_id, steam_id, charsmax(steam_id));
  
	json_len += formatex(json[json_len], charsmax(json) - json_len, "^"type^": ^"message^",");
	json_len += formatex(json[json_len], charsmax(json) - json_len, "^"type_code^": %i,", WEBHOOK_TYPE_CODE_MESSAGE);
	json_len += formatex(json[json_len], charsmax(json) - json_len, "^"nick^": ^"%s^",", owner_name);
	json_len += formatex(json[json_len], charsmax(json) - json_len, "^"message^": ^"%s^",", con_cmd_text);
	json_len += formatex(json[json_len], charsmax(json) - json_len, "^"team^": %i,", owner_team);
	json_len += formatex(json[json_len], charsmax(json) - json_len, "^"channel^": ^"%s^",", __saytext_teams[channel_in_use]);
	json_len += formatex(json[json_len], charsmax(json) - json_len, "^"steam_id^": ^"%s^"", steam_id);
  
	json_len += formatex(json[json_len], charsmax(json) - json_len, "}");

	ezhttp_option_set_body(options_id, json)

	ezhttp_post(__cvar_str_list[_webhook_url], "HTTPCompleteChat", options_id)
	
	return PLUGIN_CONTINUE;
}

//-----------------------------------------

public SetMsgModeNotify(owner_id) {
	new msgmode[64];
	formatex(msgmode, charsmax(msgmode), "messagemode %s", MESSAGEMODE_NAME);
	client_cmd(owner_id, msgmode);
}

//-----------------------------------------

public MessageModeCallback(owner_id) {
	if(!is_user_connected(owner_id)) return PLUGIN_HANDLED;

	new message[128];
	read_args(message, charsmax(message));
	
	remove_quotes(message);
	trim(message);
	
	if(!message[0]) return PLUGIN_HANDLED;
	
	new owner_name[MAX_NAME_LENGTH];
	get_user_name(owner_id, owner_name, charsmax(owner_name));
	
	client_print(owner_id, print_chat, "Сообщение отправлено");
	
	// send to discord webhook
	new EzHttpOptions:options_id = ezhttp_create_options()
	
	ezhttp_option_set_header(options_id, "Authorization", __cvar_str_list[_webhook_token])
	ezhttp_option_set_header(options_id, "Content-Type", "application/json")
  
	new json[1024];
	new json_len = 0;
  
	json_len += formatex(json[json_len], charsmax(json) - json_len, "{");
	
	replace_all(owner_name, charsmax(owner_name), "\\", "\\\\");
	replace_all(owner_name, charsmax(owner_name), "^"", "'");
	replace_all(message, charsmax(message), "\\", "\\\\");
	replace_all(message, charsmax(message), "^"", "'");
	
	new steam_id[64];
	get_user_authid(owner_id, steam_id, charsmax(steam_id));
  
	json_len += formatex(json[json_len], charsmax(json) - json_len, "^"type^": ^"notify^",");
	json_len += formatex(json[json_len], charsmax(json) - json_len, "^"nick^": ^"%s^",", owner_name);
	json_len += formatex(json[json_len], charsmax(json) - json_len, "^"message^": ^"%s^",", message);
	json_len += formatex(json[json_len], charsmax(json) - json_len, "^"steam_id^": ^"%s^"", steam_id);
  
	json_len += formatex(json[json_len], charsmax(json) - json_len, "}");

	ezhttp_option_set_body(options_id, json)

	ezhttp_post(__cvar_str_list[_webhook_url], "HTTPCompleteChat", options_id)
	
	return PLUGIN_HANDLED;
}

public OnDeathMsg() {
	ScheduleInfoPush();
}

public OnTeamInfo() {
	ScheduleInfoPush();
}

ScheduleInfoPush(Float:delay = INFO_PUSH_DEBOUNCE_SEC) {
	#if !INFO_WEBHOOK_ENABLED
		return;
	#endif

	if(task_exists(TASK_INFO_PUSH)) return;
	set_task(delay, "SendInfoSnapshotTask", TASK_INFO_PUSH);
}

stock bool:TryAppendJsonf(json[], json_size, &json_len, const fmt[], any:...) {
	// vformat ожидает maxlen в формате charsmax (без завершающего '\0'),
	// поэтому оставляем один байт под null-terminator.
	new remaining = json_size - json_len - 1;
	if(remaining <= 0) {
		return false;
	}

	new written = vformat(json[json_len], remaining, fmt, 5);
	if(written < 0 || written > remaining) {
		return false;
	}

	json_len += written;
	return true;
}

public SendInfoSnapshotTask() {
	SendInfoWebhook();
}

public InfoHeartbeatTask() {
	SendInfoWebhook();
}

SendInfoWebhook() {
	new Float:now = get_gametime();
	if(g_info_request_in_flight) {
		g_info_skip_inflight++;
		return;
	}

	if(now - g_info_last_send_time < INFO_MIN_POST_INTERVAL_SEC) {
		g_info_skip_rate++;
		return;
	}

	// send to discord webhook
	new EzHttpOptions:options_id = ezhttp_create_options()
	if(!options_id) {
		server_print("[ultrahc_discord] info webhook build failed: options alloc");
		return;
	}
	
	ezhttp_option_set_header(options_id, "Authorization", __cvar_str_list[_webhook_token])
	ezhttp_option_set_header(options_id, "Content-Type", "application/json")
  
	new json[INFO_JSON_LENGTH];
	new json_len = 0;
	new bool:payload_truncated = false;
  
	if(!TryAppendJsonf(json, sizeof(json), json_len, "{")) {
		server_print("[ultrahc_discord] info webhook build failed: cannot start json");
		return;
	}
	
	new map_name[32];
	get_mapname(map_name, charsmax(map_name));
	replace_all(map_name, charsmax(map_name), "\\", "\\\\");
	replace_all(map_name, charsmax(map_name), "^"", "'");
  
	if(!TryAppendJsonf(json, sizeof(json), json_len, "^"type^":^"info^",^"type_code^":%i,", WEBHOOK_TYPE_CODE_INFO)) {
		server_print("[ultrahc_discord] info webhook build failed: cannot append type");
		return;
	}
	if(!TryAppendJsonf(json, sizeof(json), json_len, "^"map^":^"%s^",", map_name)) {
		server_print("[ultrahc_discord] info webhook build failed: cannot append map");
		return;
	}
	
	if(!TryAppendJsonf(json, sizeof(json), json_len, "^"current_players^":[")) {
		server_print("[ultrahc_discord] info webhook build failed: cannot append current_players start");
		return;
	}

	new added_players = 0;
	new player_count = 0;
	for(new id = 1; id <= MaxClients; id++) {
		if(!is_user_connected(id) || is_user_hltv(id)) continue;
		player_count++;
	}

	#if INFO_INCLUDE_PLAYERS
		for(new id = 1; id <= MaxClients; id++) {
			if(!is_user_connected(id) || is_user_hltv(id)) continue;
			
			new user_name[MAX_NAME_LENGTH];
			get_user_name(id, user_name, charsmax(user_name));
			
			new user_auth[64];
			get_user_authid(id, user_auth, charsmax(user_auth));
			
			replace_all(user_name, charsmax(user_name), "\\", "\\\\");
			replace_all(user_name, charsmax(user_name), "^"", "'");
			replace_all(user_auth, charsmax(user_auth), "\\", "\\\\");
			replace_all(user_auth, charsmax(user_auth), "^"", "'");
			
			new user_frags = get_user_frags(id);
			new user_deaths = get_user_deaths(id);
			new user_team = get_user_team(id);

			new player_json[384];
			new player_json_len = formatex(
				player_json,
				charsmax(player_json),
				"{^"name^":^"%s^",^"steam_id^":^"%s^",^"stats^":[%i, %i, %i]}",
				user_name,
				user_auth,
				user_frags,
				user_deaths,
				user_team
			);

			// Если запись игрока не помещается в буфер строки, пропускаем оставшихся, чтобы не сломать JSON.
			if(player_json_len <= 0 || player_json_len >= charsmax(player_json)) {
				payload_truncated = true;
				break;
			}

			new required = player_json_len;
			if(added_players > 0) {
				required += 1;
			}

			// Резервируем место под закрытие массива/объекта и max_players.
			if(json_len + required + 64 >= sizeof(json)) {
				payload_truncated = true;
				break;
			}

			if(added_players > 0) {
				if(!TryAppendJsonf(json, sizeof(json), json_len, ",")) {
					payload_truncated = true;
					break;
				}
			}

			if(!TryAppendJsonf(json, sizeof(json), json_len, "%s", player_json)) {
				payload_truncated = true;
				break;
			}
			added_players++;
		}
	#endif
	
	if(!TryAppendJsonf(json, sizeof(json), json_len, "],")) {
		server_print("[ultrahc_discord] info webhook build failed: cannot close players array");
		return;
	}

	if(!TryAppendJsonf(json, sizeof(json), json_len, "^"player_count^":%i,", player_count)) {
		server_print("[ultrahc_discord] info webhook build failed: cannot append player_count");
		return;
	}
	
	if(!TryAppendJsonf(json, sizeof(json), json_len, "^"max_players^":%i", MaxClients)) {
		server_print("[ultrahc_discord] info webhook build failed: cannot append max_players");
		return;
	}
  
	if(!TryAppendJsonf(json, sizeof(json), json_len, "}")) {
		server_print("[ultrahc_discord] info webhook build failed: cannot close json");
		return;
	}

	if(payload_truncated) {
		server_print("[ultrahc_discord] info webhook payload truncated: players=%d", added_players);
	}

	ezhttp_option_set_body(options_id, json)

	g_info_request_in_flight = true;
	g_info_last_send_time = now;
	ezhttp_post(__cvar_str_list[_webhook_url], "HTTPCompleteInfo", options_id)
}

//-----------------------------------------

public HTTPCompleteChat(EzHttpRequest:request_id) {
	if (ezhttp_get_error_code(request_id) != EZH_OK) {
      new error[64];
      ezhttp_get_error_message(request_id, error, charsmax(error));
      server_print("Response error(chat): %s", error);
      return;
  }

	new data[512];
	ezhttp_get_data(request_id, data, charsmax(data));
	server_print("Response data(chat): %s", data);
}

public HTTPCompleteInfo(EzHttpRequest:request_id) {
	g_info_request_in_flight = false;

	if (ezhttp_get_error_code(request_id) != EZH_OK) {
      new error[64];
      ezhttp_get_error_message(request_id, error, charsmax(error));
      server_print("Response error(info): %s", error);
      return;
  }

	new data[128];
	ezhttp_get_data(request_id, data, charsmax(data));
	server_print("Response data(info): %s", data);
}

//-----------------------------------------

public HookDiagCmd() {
	server_print(
		"[ultrahc_discord] diag ok build=%s info_inflight=%d skip_inflight=%d skip_rate=%d",
		PLUGIN_VERSION,
		g_info_request_in_flight ? 1 : 0,
		g_info_skip_inflight,
		g_info_skip_rate
	);
	return PLUGIN_HANDLED;
}

public HookDbgPlayersCmd() {
	new connected_non_hltv = 0;
	new humans = 0;
	new bots = 0;
	new hltv = 0;

	for(new id = 1; id <= MaxClients; id++) {
		if(!is_user_connected(id)) continue;

		new name[MAX_NAME_LENGTH];
		get_user_name(id, name, charsmax(name));

		if(is_user_hltv(id)) {
			hltv++;
			server_print("[ultrahc_discord][dbg] slot=%d hltv=1 bot=%d name='%s'", id, is_user_bot(id), name);
			continue;
		}

		connected_non_hltv++;
		if(is_user_bot(id)) {
			bots++;
		} else {
			humans++;
		}

		server_print("[ultrahc_discord][dbg] slot=%d hltv=0 bot=%d name='%s'", id, is_user_bot(id), name);
	}

	server_print(
		"[ultrahc_discord][dbg] players summary non_hltv=%d humans=%d bots=%d hltv=%d max=%d",
		connected_non_hltv,
		humans,
		bots,
		hltv,
		MaxClients
	);

	return PLUGIN_HANDLED;
}

//-----------------------------------------

public HookChangeMapCmd() {
	new map[32];
	read_args(map, charsmax(map));
	
	trim(map);
	remove_quotes(map);
	
	if(!map[0])
		server_cmd("restart");
	else
		server_cmd("amx_map %s", map);
}

//-----------------------------------------

public HookGetinfoCmd() {
	#if INFO_WEBHOOK_ENABLED
		SendInfoWebhook();
	#else
		server_print("[ultrahc_discord] info webhook disabled");
	#endif
	return PLUGIN_HANDLED;
}

//-----------------------------------------

PrintMapsBegin(const mode[]) {
	server_print("%s %s", MAPS_OUTPUT_BEGIN, mode);
}

PrintMapsEnd(count) {
	server_print("%s %i", MAPS_OUTPUT_END, count);
}

bool:IsBspFilename(const file_name[]) {
	new name_len = strlen(file_name);
	if(name_len <= 4) return false;

	new ext_pos = containi(file_name, ".bsp");
	if(ext_pos < 0) return false;

	return (ext_pos == (name_len - 4));
}

PrintRotationMapList() {
	PrintMapsBegin("rotation");

	#if defined _map_manager_core_included
		new file_path[256];
		get_localinfo("amxx_configsdir", file_path, charsmax(file_path));
		formatex(file_path, charsmax(file_path), "%s/%s", file_path, MAP_ROTATION_FILE);

		new file = fopen(file_path, "rt");
		if(!file) {
			server_print("%s rotation_file_unavailable", MAPS_OUTPUT_ERROR);
			PrintMapsEnd(0);
			return;
		}

		new line[MAP_ENTRY_LENGTH];
		new map_name[64];
		new count = 0;
		while(!feof(file)) {
			fgets(file, line, charsmax(line));
			trim(line);

			if(!line[0]) continue;
			if(line[0] == ';' || line[0] == '#') continue;
			if(line[0] == '/' && line[1] == '/') continue;

			parse(line, map_name, charsmax(map_name));
			if(!map_name[0]) continue;

			server_print("%s", map_name);
			count++;
		}

		fclose(file);
		PrintMapsEnd(count);
	#else
		server_print("%s map_manager_not_enabled", MAPS_OUTPUT_ERROR);
		PrintMapsEnd(0);
	#endif
}

PrintInstalledMapList() {
	PrintMapsBegin("installed");

	new entry_name[MAP_ENTRY_LENGTH];
	new FileType:file_type = FileType_Unknown;
	new dir_handle = open_dir("maps", entry_name, charsmax(entry_name), file_type);

	if(!dir_handle) {
		server_print("%s maps_dir_unavailable", MAPS_OUTPUT_ERROR);
		PrintMapsEnd(0);
		return;
	}

	new count = 0;
	do {
		if(file_type != FileType_File) continue;
		if(!IsBspFilename(entry_name)) continue;

		entry_name[strlen(entry_name) - 4] = 0;
		server_print("%s", entry_name);
		count++;
	} while(next_file(dir_handle, entry_name, charsmax(entry_name), file_type));

	close_dir(dir_handle);
	PrintMapsEnd(count);
}

public HookGetMapsCmd() {
	new mode[MAP_QUERY_MODE_LENGTH];
	read_argv(1, mode, charsmax(mode));

	trim(mode);
	remove_quotes(mode);

	if(!mode[0] || equali(mode, "rotation")) {
		PrintRotationMapList();
		return PLUGIN_HANDLED;
	}

	if(equali(mode, "installed")) {
		PrintInstalledMapList();
		return PLUGIN_HANDLED;
	}

	PrintMapsBegin("invalid");
	server_print("%s unsupported_mode", MAPS_OUTPUT_ERROR);
	PrintMapsEnd(0);

	return PLUGIN_HANDLED;
}

//-----------------------------------------

public HookKickPlayerCmd() {
	new cmd_text[150];
	read_args(cmd_text, charsmax(cmd_text));

	new player_to_kick[32];
	new reason[128];
	parse(cmd_text, player_to_kick, charsmax(player_to_kick), reason, charsmax(reason));

	server_cmd("amx_kick ^"%s^" ^"%s^"", player_to_kick, reason);
}

//-----------------------------------------
public HookMsgFromDs() {
	new cmd_text[DS_SEND_CMD_TEXT_LENGTH];
	read_args(cmd_text, charsmax(cmd_text));
	server_print("[ultrahc_discord][dbg] HookMsgFromDs raw='%s'", cmd_text);
	
	new author[DS_SEND_AUTHOR_LENGTH];
	new msg[DS_SEND_MESSAGE_LENGTH];

	// Prefer argv parsing for quoted args: ultrahc_ds_send_msg "author" "message"
	read_argv(1, author, charsmax(author));
	read_argv(2, msg, charsmax(msg));

	// Fallback for edge cases where argv is unavailable/empty
	if(!author[0] || !msg[0]) {
		parse(cmd_text, author, charsmax(author), msg, charsmax(msg));
	}

	if(!author[0] || !msg[0]) {
		server_print("[ultrahc_discord][dbg] HookMsgFromDs empty parsed values");
		return PLUGIN_HANDLED;
	}

	new players[32], num;
	get_players(players, num, "ch");
	server_print("[ultrahc_discord][dbg] HookMsgFromDs recipients=%d", num);

	replace_all(author, charsmax(author), "^"", "'");
	replace_all(msg, charsmax(msg), "^"", "'");
	replace_all(author, charsmax(author), ";", ",");
	replace_all(msg, charsmax(msg), ";", ",");

	client_print(0, print_chat, "[Discord] %s : %s", author, msg);
	server_cmd("say [Discord] %s : %s", author, msg);
	server_exec();
	server_print("[ultrahc_discord][dbg] HookMsgFromDs say sent");

	return PLUGIN_HANDLED;
}

public HookMsgFromDsDefault() {
	server_print("[ultrahc_discord][dbg] HookMsgFromDsDefault route");
	return HookMsgFromDsOld();
}

public HookMsgFromDsOld() {
	new cmd_text[DS_SEND_CMD_TEXT_LENGTH];
	read_args(cmd_text, charsmax(cmd_text));
	server_print("[ultrahc_discord][dbg] HookMsgFromDsOld raw='%s'", cmd_text);

	new author[DS_SEND_AUTHOR_LENGTH];
	new msg[DS_SEND_MESSAGE_LENGTH];
	parse(cmd_text, author, charsmax(author), msg, charsmax(msg));

	if(!author[0] || !msg[0]) {
		server_print("[ultrahc_discord][dbg] HookMsgFromDsOld empty parsed values");
		return PLUGIN_HANDLED;
	}

	client_print_color(0, print_team_blue, "%s ^3%s^1 : ^4%s", DISCORD_PREFIX, author, msg);
	server_print("[ultrahc_discord][dbg] HookMsgFromDsOld client_print_color sent");
	return PLUGIN_HANDLED;
}

//-----------------------------------------

#if defined _map_manager_core_included
	public plugin_cfg() {
		new map_file[32];
		copy(map_file, charsmax(map_file), MAP_ROTATION_FILE);
		mapm_load_maplist(map_file)
	}

	public HookReloadMapList() {
		new sql_request[512] = "SELECT map_name, min_players, max_players, priority FROM maps WHERE activated=1";
		
		SQL_ThreadQuery(__sql_handle, "SQLHandlerForMapList", sql_request);
	}

	public SQLHandlerForMapList(failstate, Handle:query, error[], errnum, data[], size, queuetime) {
		new file_path[256]; 
		get_localinfo("amxx_configsdir", file_path, charsmax(file_path));
		formatex(file_path, charsmax(file_path), "%s/%s", file_path, MAP_ROTATION_FILE);
		
		new file = fopen(file_path, "w");
		
		if(!file) {
			server_print("ULTRAHC_DISCORD: Can't create/open map list");
			return;
		}
		
		while(SQL_MoreResults(query)) {
			new map_name[MAPNAME_LENGTH];
			SQL_ReadResult(query, 0, map_name, charsmax(map_name));
			
			new min_players = SQL_ReadResult(query, 1);
			new max_players = SQL_ReadResult(query, 2);
			new priority = SQL_ReadResult(query, 3);
			
			new str_to_put[128];
			
			formatex(str_to_put, charsmax(str_to_put), "%s %i %i %i^n", map_name, min_players, max_players, priority);
			
			fputs(file, str_to_put);
		
			SQL_NextRow(query);
		}
		
		fclose(file);
	}

#endif

//-----------------------------------------

GetChannel(is_say_team, is_player_alive, player_team) {
	new channel;
	if(is_say_team) {
		switch(player_team) {
			case CS_TEAM_T:
				channel = (is_player_alive) ? 2 : 3;
			case CS_TEAM_CT:
				channel = (is_player_alive) ? 4 : 5;
			default:
				channel = 6;
		}	
	} else {
		channel = (player_team == _:CS_TEAM_SPECTATOR) ? 7 : (!is_player_alive ? 1 : 0);
	}
	
	return channel;
}
