// Existing upstream detection requests and verdict rules, maintained as declarative data.
// Regex descriptors retain their flags; this module never sends a request by itself.
export const categories = [
  "basic",
  "ai",
  "global",
  "na",
  "eu",
  "hk",
  "tw",
  "jp"
];
export const regionTests = [
  {
    "id": "ip",
    "name": "出口 IP",
    "cat": "basic",
    "steps": [
      {
        "id": "a",
        "url": "https://www.cloudflare.com/cdn-cgi/trace",
        "maxBody": 4096
      }
    ],
    "rules": [
      {
        "then": {
          "state": "info",
          "label": {
            "re": [
              {
                "$": "a.body"
              },
              {
                "pattern": "\\bip=(\\S+)",
                "flags": ""
              }
            ]
          },
          "region": {
            "re": [
              {
                "$": "a.body"
              },
              {
                "pattern": "\\bloc=([A-Z]{2})",
                "flags": ""
              }
            ]
          }
        }
      }
    ]
  },
  {
    "id": "udp-ip",
    "name": "UDP出口IP",
    "cat": "basic",
    "steps": [
      {
        "id": "a",
        "udp": true
      }
    ],
    "rules": [
      {
        "when": {
          "has": [
            {
              "$": "a.body"
            },
            "udp=0"
          ]
        },
        "then": {
          "state": "info",
          "label": "UDP 未开启"
        }
      },
      {
        "when": {
          "has": [
            {
              "$": "a.body"
            },
            "udp=2"
          ]
        },
        "then": {
          "state": "fail",
          "label": "UDP 失败",
          "tooltip": {
            "re": [
              {
                "$": "a.body"
              },
              {
                "pattern": "err=([\\s\\S]*)",
                "flags": ""
              }
            ]
          }
        }
      },
      {
        "when": {
          "has": [
            {
              "$": "a.body"
            },
            "udp=3"
          ]
        },
        "then": {
          "state": "partial",
          "label": "UDP 部分可用",
          "tooltip": {
            "re": [
              {
                "$": "a.body"
              },
              {
                "pattern": "err=([\\s\\S]*)",
                "flags": ""
              }
            ]
          }
        }
      },
      {
        "when": {
          "present": {
            "re": [
              {
                "$": "a.body"
              },
              {
                "pattern": "\\bip=(\\S+)",
                "flags": ""
              }
            ]
          }
        },
        "then": {
          "state": "info",
          "label": {
            "re": [
              {
                "$": "a.body"
              },
              {
                "pattern": "\\bip=(\\S+)",
                "flags": ""
              }
            ]
          },
          "extra": {
            "via": {
              "re": [
                {
                  "$": "a.body"
                },
                {
                  "pattern": "\\bvia=(\\S+)",
                  "flags": ""
                }
              ]
            }
          }
        }
      },
      {
        "then": {
          "state": "fail",
          "label": "无法获取"
        }
      }
    ]
  },
  {
    "id": "isp",
    "name": "ISP",
    "cat": "basic",
    "steps": [
      {
        "id": "a",
        "url": "https://surfshark.com/api/v1/server/user?_t=${ts}",
        "maxBody": 8192
      }
    ],
    "rules": [
      {
        "when": {
          "present": {
            "json": [
              "a",
              "isp"
            ]
          }
        },
        "then": {
          "state": "info",
          "label": {
            "concat": [
              {
                "json": [
                  "a",
                  "isp"
                ]
              },
              " / ",
              {
                "json": [
                  "a",
                  "city"
                ]
              },
              ", ",
              {
                "json": [
                  "a",
                  "region"
                ]
              },
              " · ",
              {
                "upper": {
                  "json": [
                    "a",
                    "countryCode"
                  ]
                }
              }
            ]
          },
          "region": {
            "upper": {
              "json": [
                "a",
                "countryCode"
              ]
            }
          }
        }
      },
      {
        "then": {
          "state": "fail",
          "label": "无法获取"
        }
      }
    ]
  },
  {
    "id": "ip-type",
    "name": "IP 属性",
    "cat": "basic",
    "steps": [
      {
        "id": "a",
        "url": "https://www.cloudflare.com/cdn-cgi/trace",
        "maxBody": 4096,
        "sharedKey": "ip"
      },
      {
        "id": "b",
        "url": "https://api.ipregistry.co/${ip}?hostname=true&key=sb69ksjcajfs4c",
        "needs": [
          "a"
        ],
        "vars": {
          "ip": {
            "re": [
              {
                "$": "a.body"
              },
              {
                "pattern": "\\bip=(\\S+)",
                "flags": ""
              }
            ]
          }
        },
        "headers": {
          "Origin": "https://ipregistry.co",
          "Referer": "https://ipregistry.co/"
        },
        "maxBody": 8192
      }
    ],
    "rules": [
      {
        "when": {
          "present": {
            "json": [
              "b",
              "security"
            ]
          }
        },
        "then": {
          "state": "info",
          "label": {
            "concat": [
              {
                "map": [
                  {
                    "json": [
                      "b",
                      "connection.type"
                    ]
                  },
                  {
                    "hosting": "机房",
                    "isp": "家宽",
                    "business": "商业",
                    "education": "教育",
                    "government": "政府",
                    "banking": "银行",
                    "mobile": "手机"
                  }
                ]
              },
              " / ",
              {
                "map": [
                  {
                    "json": [
                      "b",
                      "company.type"
                    ]
                  },
                  {
                    "hosting": "机房",
                    "isp": "家宽",
                    "business": "商业",
                    "education": "教育",
                    "government": "政府",
                    "banking": "银行",
                    "mobile": "手机"
                  }
                ]
              },
              {
                "concat": [
                  {
                    "if": [
                      {
                        "eq": [
                          {
                            "json": [
                              "b",
                              "security.is_vpn"
                            ]
                          },
                          true
                        ]
                      },
                      " · VPN",
                      ""
                    ]
                  },
                  {
                    "if": [
                      {
                        "eq": [
                          {
                            "json": [
                              "b",
                              "security.is_proxy"
                            ]
                          },
                          true
                        ]
                      },
                      " · Proxy",
                      ""
                    ]
                  },
                  {
                    "if": [
                      {
                        "eq": [
                          {
                            "json": [
                              "b",
                              "security.is_tor"
                            ]
                          },
                          true
                        ]
                      },
                      " · Tor",
                      ""
                    ]
                  },
                  {
                    "if": [
                      {
                        "eq": [
                          {
                            "json": [
                              "b",
                              "security.is_anonymous"
                            ]
                          },
                          true
                        ]
                      },
                      " · 匿名",
                      ""
                    ]
                  }
                ]
              }
            ]
          },
          "region": {
            "json": [
              "b",
              "location.country.code"
            ]
          }
        }
      },
      {
        "then": {
          "state": "fail",
          "label": "无法获取"
        }
      }
    ]
  },
  {
    "id": "ip-risk",
    "name": "IP 风险",
    "cat": "basic",
    "steps": [
      {
        "id": "a",
        "url": "https://www.cloudflare.com/cdn-cgi/trace",
        "maxBody": 4096,
        "sharedKey": "ip"
      },
      {
        "id": "b",
        "url": "https://db-ip.com/",
        "maxBody": 65536
      },
      {
        "id": "c",
        "url": "https://api.db-ip.com/v2/${dbipKey}/self",
        "needs": [
          "b"
        ],
        "vars": {
          "dbipKey": {
            "re": [
              {
                "$": "b.body"
              },
              {
                "pattern": "data-api-key=\"([^\"]+)\"",
                "flags": ""
              }
            ]
          }
        },
        "headers": {
          "Origin": "https://db-ip.com",
          "Referer": "https://db-ip.com/"
        },
        "maxBody": 32768
      },
      {
        "id": "d",
        "url": "https://ipapi.com/ip_api.php?ip=${ip}",
        "needs": [
          "a"
        ],
        "vars": {
          "ip": {
            "re": [
              {
                "$": "a.body"
              },
              {
                "pattern": "\\bip=(\\S+)",
                "flags": ""
              }
            ]
          }
        },
        "maxBody": 32768
      }
    ],
    "rules": [
      {
        "when": {
          "any": [
            {
              "present": {
                "json": [
                  "c",
                  "threatLevel"
                ]
              }
            },
            {
              "present": {
                "json": [
                  "d",
                  "security.threat_level"
                ]
              }
            }
          ]
        },
        "then": {
          "state": "info",
          "label": {
            "if": [
              {
                "all": [
                  {
                    "present": {
                      "json": [
                        "c",
                        "threatLevel"
                      ]
                    }
                  },
                  {
                    "present": {
                      "json": [
                        "d",
                        "security.threat_level"
                      ]
                    }
                  }
                ]
              },
              {
                "if": [
                  {
                    "eq": [
                      {
                        "lower": {
                          "json": [
                            "c",
                            "threatLevel"
                          ]
                        }
                      },
                      {
                        "lower": {
                          "json": [
                            "d",
                            "security.threat_level"
                          ]
                        }
                      }
                    ]
                  },
                  {
                    "map": [
                      {
                        "lower": {
                          "json": [
                            "c",
                            "threatLevel"
                          ]
                        }
                      },
                      {
                        "low": "低",
                        "medium": "中",
                        "high": "高"
                      }
                    ]
                  },
                  {
                    "concat": [
                      {
                        "map": [
                          {
                            "lower": {
                              "json": [
                                "c",
                                "threatLevel"
                              ]
                            }
                          },
                          {
                            "low": "低",
                            "medium": "中",
                            "high": "高"
                          }
                        ]
                      },
                      " / ",
                      {
                        "map": [
                          {
                            "lower": {
                              "json": [
                                "d",
                                "security.threat_level"
                              ]
                            }
                          },
                          {
                            "low": "低",
                            "medium": "中",
                            "high": "高"
                          }
                        ]
                      }
                    ]
                  }
                ]
              },
              {
                "first": [
                  {
                    "map": [
                      {
                        "lower": {
                          "json": [
                            "c",
                            "threatLevel"
                          ]
                        }
                      },
                      {
                        "low": "低",
                        "medium": "中",
                        "high": "高"
                      }
                    ]
                  },
                  {
                    "map": [
                      {
                        "lower": {
                          "json": [
                            "d",
                            "security.threat_level"
                          ]
                        }
                      },
                      {
                        "low": "低",
                        "medium": "中",
                        "high": "高"
                      }
                    ]
                  }
                ]
              }
            ]
          },
          "region": {
            "re": [
              {
                "$": "a.body"
              },
              {
                "pattern": "\\bloc=([A-Z]{2})",
                "flags": ""
              }
            ]
          },
          "tooltip": {
            "concat": [
              {
                "if": [
                  {
                    "present": {
                      "json": [
                        "c",
                        "threatLevel"
                      ]
                    }
                  },
                  {
                    "concat": [
                      "db-ip",
                      ": ",
                      {
                        "map": [
                          {
                            "lower": {
                              "json": [
                                "c",
                                "threatLevel"
                              ]
                            }
                          },
                          {
                            "low": "低",
                            "medium": "中",
                            "high": "高"
                          }
                        ]
                      },
                      " | ",
                      {
                        "json": [
                          "c",
                          "isp"
                        ]
                      }
                    ]
                  },
                  ""
                ]
              },
              {
                "if": [
                  {
                    "all": [
                      {
                        "present": {
                          "json": [
                            "c",
                            "threatLevel"
                          ]
                        }
                      },
                      {
                        "present": {
                          "json": [
                            "d",
                            "security.threat_level"
                          ]
                        }
                      }
                    ]
                  },
                  "\n",
                  ""
                ]
              },
              {
                "if": [
                  {
                    "present": {
                      "json": [
                        "d",
                        "security.threat_level"
                      ]
                    }
                  },
                  {
                    "concat": [
                      "ipapi",
                      ": ",
                      {
                        "map": [
                          {
                            "lower": {
                              "json": [
                                "d",
                                "security.threat_level"
                              ]
                            }
                          },
                          {
                            "low": "低",
                            "medium": "中",
                            "high": "高"
                          }
                        ]
                      },
                      " | ",
                      {
                        "json": [
                          "d",
                          "connection.isp"
                        ]
                      }
                    ]
                  },
                  ""
                ]
              }
            ]
          },
          "extra": {
            "isp": {
              "first": [
                {
                  "json": [
                    "c",
                    "isp"
                  ]
                },
                {
                  "json": [
                    "d",
                    "connection.isp"
                  ]
                }
              ]
            },
            "city": {
              "first": [
                {
                  "json": [
                    "c",
                    "city"
                  ]
                },
                {
                  "json": [
                    "d",
                    "city"
                  ]
                }
              ]
            },
            "region": {
              "first": [
                {
                  "json": [
                    "c",
                    "stateProv"
                  ]
                },
                {
                  "json": [
                    "d",
                    "region_name"
                  ]
                }
              ]
            },
            "country": {
              "first": [
                {
                  "json": [
                    "c",
                    "countryName"
                  ]
                },
                {
                  "json": [
                    "d",
                    "country_name"
                  ]
                }
              ]
            }
          }
        }
      },
      {
        "then": {
          "state": "fail",
          "label": "无法获取"
        }
      }
    ]
  },
  {
    "id": "ipinfo",
    "name": "IPinfo",
    "cat": "basic",
    "steps": [
      {
        "id": "a",
        "url": "https://www.cloudflare.com/cdn-cgi/trace",
        "maxBody": 4096,
        "sharedKey": "ip"
      },
      {
        "id": "b",
        "url": "https://ipinfo.io/widget/demo/${ip}",
        "needs": [
          "a"
        ],
        "vars": {
          "ip": {
            "re": [
              {
                "$": "a.body"
              },
              {
                "pattern": "\\bip=(\\S+)",
                "flags": ""
              }
            ]
          }
        },
        "headers": {
          "Referer": "https://ipinfo.io/"
        },
        "maxBody": 16384
      }
    ],
    "rules": [
      {
        "when": {
          "present": {
            "json": [
              "b",
              "data.ip"
            ]
          }
        },
        "then": {
          "state": "info",
          "label": {
            "json": [
              "b",
              "data.company.name"
            ]
          },
          "region": {
            "json": [
              "b",
              "data.country"
            ]
          },
          "extra": {
            "isp": {
              "json": [
                "b",
                "data.company.name"
              ]
            },
            "city": {
              "json": [
                "b",
                "data.city"
              ]
            },
            "region": {
              "json": [
                "b",
                "data.region"
              ]
            },
            "type": {
              "map": [
                {
                  "json": [
                    "b",
                    "data.company.type"
                  ]
                },
                {
                  "hosting": "机房",
                  "isp": "家宽",
                  "business": "商业",
                  "education": "教育",
                  "government": "政府",
                  "banking": "银行",
                  "mobile": "手机"
                }
              ]
            },
            "type2": {
              "map": [
                {
                  "json": [
                    "b",
                    "data.asn.type"
                  ]
                },
                {
                  "hosting": "机房",
                  "isp": "家宽",
                  "business": "商业",
                  "education": "教育",
                  "government": "政府",
                  "banking": "银行",
                  "mobile": "手机"
                }
              ]
            },
            "is_vpn": {
              "json": [
                "b",
                "data.privacy.vpn"
              ]
            },
            "is_proxy": {
              "json": [
                "b",
                "data.privacy.proxy"
              ]
            },
            "is_tor": {
              "json": [
                "b",
                "data.privacy.tor"
              ]
            },
            "is_hosting": {
              "json": [
                "b",
                "data.privacy.hosting"
              ]
            }
          }
        }
      },
      {
        "then": {
          "state": "fail",
          "label": "无法获取"
        }
      }
    ]
  },
  {
    "id": "ipapi",
    "name": "IPAPI",
    "cat": "basic",
    "steps": [
      {
        "id": "a",
        "url": "https://www.cloudflare.com/cdn-cgi/trace",
        "maxBody": 4096,
        "sharedKey": "ip"
      },
      {
        "id": "b",
        "url": "https://api.ipapi.is/?q=${ip}",
        "needs": [
          "a"
        ],
        "vars": {
          "ip": {
            "re": [
              {
                "$": "a.body"
              },
              {
                "pattern": "\\bip=(\\S+)",
                "flags": ""
              }
            ]
          }
        },
        "maxBody": 32768
      }
    ],
    "rules": [
      {
        "when": {
          "present": {
            "json": [
              "b",
              "ip"
            ]
          }
        },
        "then": {
          "state": "info",
          "label": {
            "json": [
              "b",
              "company_name"
            ]
          },
          "region": {
            "json": [
              "b",
              "cc"
            ]
          },
          "extra": {
            "isp": {
              "json": [
                "b",
                "company_name"
              ]
            },
            "is_vpn": {
              "json": [
                "b",
                "is_vpn"
              ]
            },
            "is_proxy": {
              "json": [
                "b",
                "is_proxy"
              ]
            },
            "is_tor": {
              "json": [
                "b",
                "is_tor"
              ]
            },
            "is_hosting": {
              "json": [
                "b",
                "is_datacenter"
              ]
            },
            "is_abuser": {
              "json": [
                "b",
                "is_abuser"
              ]
            }
          }
        }
      },
      {
        "then": {
          "state": "fail",
          "label": "无法获取"
        }
      }
    ]
  },
  {
    "id": "chatgpt",
    "name": "ChatGPT",
    "cat": "ai",
    "steps": [
      {
        "id": "a",
        "url": "https://chatgpt.com/",
        "follow": false,
        "maxBody": 8192
      },
      {
        "id": "b",
        "url": "https://ios.chat.openai.com",
        "maxBody": 16384
      },
      {
        "id": "c",
        "url": "https://chatgpt.com/backend-anon/me",
        "needs": [
          "a"
        ],
        "maxBody": 4096,
        "vars": {
          "ck": {
            "cookies": "a"
          }
        },
        "headers": {
          "cookie": "${ck}"
        }
      },
      {
        "id": "d",
        "url": "https://chatgpt.com/cdn-cgi/trace",
        "maxBody": 4096
      }
    ],
    "rules": [
      {
        "when": {
          "all": [
            {
              "eq": [
                {
                  "$": "c.status"
                },
                200
              ]
            },
            {
              "present": {
                "json": [
                  "c",
                  "country"
                ]
              }
            }
          ]
        },
        "then": {
          "state": "yes",
          "region": {
            "json": [
              "c",
              "country"
            ]
          },
          "ip": {
            "re": [
              {
                "$": "d.body"
              },
              {
                "pattern": "\\bip=([^\\s]+)",
                "flags": ""
              }
            ]
          }
        }
      },
      {
        "when": {
          "all": [
            {
              "empty": {
                "$": "a.headers.location"
              }
            },
            {
              "has": [
                {
                  "$": "b.body"
                },
                "blocked_why_headline"
              ]
            }
          ]
        },
        "then": {
          "state": "no",
          "label": "已封禁"
        }
      },
      {
        "when": {
          "all": [
            {
              "empty": {
                "$": "a.headers.location"
              }
            },
            {
              "has": [
                {
                  "$": "b.body"
                },
                "unsupported_country"
              ]
            }
          ]
        },
        "then": {
          "state": "no",
          "label": "地区不支持"
        }
      },
      {
        "when": {
          "all": [
            {
              "empty": {
                "$": "a.headers.location"
              }
            },
            {
              "match": [
                {
                  "$": "b.body"
                },
                {
                  "pattern": "\"cf_details\":\"[^\"]*\\([12]\\)",
                  "flags": ""
                }
              ]
            }
          ]
        },
        "then": {
          "state": "no",
          "label": "ISP 不允许"
        }
      },
      {
        "when": {
          "empty": {
            "$": "a.headers.location"
          }
        },
        "then": {
          "state": "no"
        }
      },
      {
        "when": {
          "match": [
            {
              "$": "b.body"
            },
            {
              "pattern": "\"cf_details\":\"[^\"]*\\([12]\\)",
              "flags": ""
            }
          ]
        },
        "then": {
          "state": "partial",
          "label": "仅网页版",
          "region": {
            "re": [
              {
                "$": "d.body"
              },
              {
                "pattern": "\\bloc=([A-Z]{2})",
                "flags": ""
              }
            ]
          },
          "ip": {
            "re": [
              {
                "$": "d.body"
              },
              {
                "pattern": "\\bip=([^\\s]+)",
                "flags": ""
              }
            ]
          }
        }
      },
      {
        "then": {
          "state": "yes",
          "region": {
            "re": [
              {
                "$": "d.body"
              },
              {
                "pattern": "\\bloc=([A-Z]{2})",
                "flags": ""
              }
            ]
          },
          "ip": {
            "re": [
              {
                "$": "d.body"
              },
              {
                "pattern": "\\bip=([^\\s]+)",
                "flags": ""
              }
            ]
          }
        }
      }
    ]
  },
  {
    "id": "claude",
    "name": "Claude",
    "cat": "ai",
    "steps": [
      {
        "id": "a",
        "url": "https://claude.ai/",
        "maxBody": 50000
      },
      {
        "id": "b",
        "url": "https://claude.ai/cdn-cgi/trace",
        "maxBody": 4096
      }
    ],
    "rules": [
      {
        "when": {
          "has": [
            {
              "$": "a.url"
            },
            "unavailable"
          ]
        },
        "then": {
          "state": "no"
        }
      },
      {
        "then": {
          "state": "yes",
          "region": {
            "re": [
              {
                "$": "b.body"
              },
              {
                "pattern": "\\bloc=([A-Z]{2})",
                "flags": ""
              }
            ]
          },
          "ip": {
            "re": [
              {
                "$": "b.body"
              },
              {
                "pattern": "\\bip=([^\\s]+)",
                "flags": ""
              }
            ]
          }
        }
      }
    ]
  },
  {
    "id": "gemini",
    "name": "Google Gemini",
    "cat": "ai",
    "steps": [
      {
        "id": "a",
        "url": "https://gemini.google.com/",
        "maxBody": 8192
      },
      {
        "id": "b",
        "url": "https://gemini.google.com/_/BardChatUi/data/batchexecute",
        "method": "POST",
        "needs": [
          "a"
        ],
        "headers": {
          "content-type": "application/x-www-form-urlencoded"
        },
        "body": "f.req=[[[\"K4WWud\",\"[[0],[\\\"en-US\\\"]]\",null,\"generic\"]]]",
        "follow": false
      }
    ],
    "rules": [
      {
        "when": {
          "any": [
            {
              "has": [
                {
                  "$": "b.headers.location"
                },
                "google.com/sorry"
              ]
            },
            {
              "has": [
                {
                  "$": "b.body"
                },
                "google.com/sorry"
              ]
            }
          ]
        },
        "then": {
          "state": "fail",
          "label": "Google 拦截"
        }
      },
      {
        "when": {
          "has": [
            {
              "$": "b.body"
            },
            "SNAPPED_OUT"
          ]
        },
        "then": {
          "state": "no"
        }
      },
      {
        "when": {
          "has": [
            {
              "$": "b.body"
            },
            "SWML_DESCRIPTION_FROM_YOUR_INTERNET_ADDRESS"
          ]
        },
        "then": {
          "state": "yes",
          "region": {
            "re": [
              {
                "$": "b.body"
              },
              {
                "pattern": "\\[\\[\\\\\"([^\"\\\\]+)\\\\\",\\\\\"SWML_DESCRIPTION",
                "flags": ""
              }
            ]
          }
        }
      },
      {
        "when": {
          "all": [
            {
              "eq": [
                {
                  "$": "a.status"
                },
                200
              ]
            },
            {
              "present": {
                "$": "a.setCookie.0"
              }
            }
          ]
        },
        "then": {
          "state": "yes"
        }
      },
      {
        "then": {
          "state": "fail",
          "label": "无法获取区域"
        }
      }
    ]
  },
  {
    "id": "copilot",
    "name": "Microsoft Copilot",
    "cat": "ai",
    "steps": [
      {
        "id": "a",
        "url": "https://copilot.microsoft.com/",
        "maxBody": 262144
      },
      {
        "id": "b",
        "url": "https://copilot.microsoft.com/turing/conversation/chats?bundleVersion=1.1342.3-cplt.12",
        "maxBody": 16384
      }
    ],
    "rules": [
      {
        "when": {
          "any": [
            {
              "eq": [
                {
                  "json": [
                    "b",
                    "result.value"
                  ]
                },
                "Success"
              ]
            },
            {
              "all": [
                {
                  "eq": [
                    {
                      "$": "b.status"
                    },
                    200
                  ]
                },
                {
                  "present": {
                    "json": [
                      "b",
                      "chats"
                    ]
                  }
                }
              ]
            }
          ]
        },
        "then": {
          "state": "yes",
          "region": {
            "first": [
              {
                "upper": {
                  "re": [
                    {
                      "$": "a.body"
                    },
                    {
                      "pattern": "RevIpCC:\"([^\"]*)\"",
                      "flags": ""
                    }
                  ]
                }
              },
              {
                "upper": {
                  "re": [
                    {
                      "$": "b.headers.x-msedge-ref"
                    },
                    {
                      "pattern": "Ref B:\\s*([A-Z]{3})",
                      "flags": ""
                    }
                  ]
                }
              },
              {
                "upper": {
                  "re": [
                    {
                      "$": "a.headers.x-msedge-ref"
                    },
                    {
                      "pattern": "Ref B:\\s*([A-Z]{3})",
                      "flags": ""
                    }
                  ]
                }
              }
            ]
          }
        }
      },
      {
        "then": {
          "state": "no"
        }
      }
    ]
  },
  {
    "id": "netflix",
    "name": "Netflix",
    "cat": "global",
    "steps": [
      {
        "id": "a",
        "url": "https://www.netflix.com/title/70195800",
        "maxBody": 5242880
      },
      {
        "id": "b",
        "url": "https://www.netflix.com/title/70140391",
        "maxBody": 5242880
      }
    ],
    "rules": [
      {
        "when": {
          "any": [
            {
              "has": [
                {
                  "$": "a.body"
                },
                "og:video"
              ]
            },
            {
              "has": [
                {
                  "$": "b.body"
                },
                "og:video"
              ]
            }
          ]
        },
        "then": {
          "state": "yes",
          "region": {
            "first": [
              {
                "re": [
                  {
                    "$": "a.body"
                  },
                  {
                    "pattern": "\"requestCountry\":\\{[^}]*\"id\":\"([A-Z]{2})\"",
                    "flags": ""
                  }
                ]
              },
              {
                "re": [
                  {
                    "$": "b.body"
                  },
                  {
                    "pattern": "\"requestCountry\":\\{[^}]*\"id\":\"([A-Z]{2})\"",
                    "flags": ""
                  }
                ]
              }
            ]
          }
        }
      },
      {
        "then": {
          "state": "partial",
          "label": "仅自制剧",
          "region": {
            "first": [
              {
                "re": [
                  {
                    "$": "a.body"
                  },
                  {
                    "pattern": "\"requestCountry\":\\{[^}]*\"id\":\"([A-Z]{2})\"",
                    "flags": ""
                  }
                ]
              },
              {
                "re": [
                  {
                    "$": "b.body"
                  },
                  {
                    "pattern": "\"requestCountry\":\\{[^}]*\"id\":\"([A-Z]{2})\"",
                    "flags": ""
                  }
                ]
              }
            ]
          }
        }
      }
    ]
  },
  {
    "id": "disney",
    "name": "Disney+",
    "cat": "global",
    "steps": [
      {
        "id": "a",
        "url": "https://disney.api.edge.bamgrid.com/devices",
        "method": "POST",
        "headers": {
          "authorization": "Bearer ZGlzbmV5JmJyb3dzZXImMS4wLjA.Cu56AgSfBTDag5NiRA81oLHkDZfu5L3CKadnefEAY84",
          "content-type": "application/json"
        },
        "body": "{\"deviceFamily\":\"browser\",\"applicationRuntime\":\"chrome\",\"deviceProfile\":\"windows\",\"attributes\":{}}"
      },
      {
        "id": "b",
        "url": "https://disney.api.edge.bamgrid.com/token",
        "method": "POST",
        "needs": [
          "a"
        ],
        "vars": {
          "assertion": {
            "json": [
              "a",
              "assertion"
            ]
          }
        },
        "headers": {
          "authorization": "Bearer ZGlzbmV5JmJyb3dzZXImMS4wLjA.Cu56AgSfBTDag5NiRA81oLHkDZfu5L3CKadnefEAY84",
          "content-type": "application/x-www-form-urlencoded"
        },
        "body": "grant_type=urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Atoken-exchange&latitude=0&longitude=0&platform=browser&subject_token=${assertion}&subject_token_type=urn%3Abamtech%3Aparams%3Aoauth%3Atoken-type%3Adevice"
      },
      {
        "id": "c",
        "url": "https://disney.api.edge.bamgrid.com/graph/v1/device/graphql",
        "method": "POST",
        "needs": [
          "b"
        ],
        "vars": {
          "refresh": {
            "json": [
              "b",
              "refresh_token"
            ]
          }
        },
        "headers": {
          "authorization": "ZGlzbmV5JmJyb3dzZXImMS4wLjA.Cu56AgSfBTDag5NiRA81oLHkDZfu5L3CKadnefEAY84",
          "content-type": "application/json"
        },
        "body": "{\"query\":\"mutation refreshToken($input: RefreshTokenInput!) { refreshToken(refreshToken: $input) { activeSession { sessionId location { countryCode } inSupportedLocation } } }\",\"variables\":{\"input\":{\"refreshToken\":\"${refresh}\"}}}"
      },
      {
        "id": "d",
        "url": "https://www.disneyplus.com",
        "maxBody": 8192
      }
    ],
    "rules": [
      {
        "when": {
          "any": [
            {
              "has": [
                {
                  "$": "b.body"
                },
                "forbidden-location"
              ]
            },
            {
              "has": [
                {
                  "$": "b.body"
                },
                "403 ERROR"
              ]
            }
          ]
        },
        "then": {
          "state": "no",
          "label": "已封禁"
        }
      },
      {
        "when": {
          "anyFailed": [
            "a",
            "b",
            "c"
          ]
        },
        "then": {
          "state": "fail",
          "label": "链路中断"
        }
      },
      {
        "when": {
          "eq": [
            {
              "re": [
                {
                  "$": "c.body"
                },
                {
                  "pattern": "\"countryCode\":\"([A-Z]{2})\"",
                  "flags": ""
                }
              ]
            },
            "JP"
          ]
        },
        "then": {
          "state": "yes",
          "region": "JP"
        }
      },
      {
        "when": {
          "all": [
            {
              "present": {
                "re": [
                  {
                    "$": "c.body"
                  },
                  {
                    "pattern": "\"countryCode\":\"([A-Z]{2})\"",
                    "flags": ""
                  }
                ]
              }
            },
            {
              "has": [
                {
                  "$": "d.url"
                },
                "unavailable"
              ]
            }
          ]
        },
        "then": {
          "state": "no",
          "label": "不可用"
        }
      },
      {
        "when": {
          "all": [
            {
              "present": {
                "re": [
                  {
                    "$": "c.body"
                  },
                  {
                    "pattern": "\"countryCode\":\"([A-Z]{2})\"",
                    "flags": ""
                  }
                ]
              }
            },
            {
              "eq": [
                {
                  "re": [
                    {
                      "$": "c.body"
                    },
                    {
                      "pattern": "\"inSupportedLocation\":(true|false)",
                      "flags": ""
                    }
                  ]
                },
                "false"
              ]
            }
          ]
        },
        "then": {
          "state": "partial",
          "label": "即将上线",
          "region": {
            "re": [
              {
                "$": "c.body"
              },
              {
                "pattern": "\"countryCode\":\"([A-Z]{2})\"",
                "flags": ""
              }
            ]
          }
        }
      },
      {
        "when": {
          "present": {
            "re": [
              {
                "$": "c.body"
              },
              {
                "pattern": "\"countryCode\":\"([A-Z]{2})\"",
                "flags": ""
              }
            ]
          }
        },
        "then": {
          "state": "yes",
          "region": {
            "re": [
              {
                "$": "c.body"
              },
              {
                "pattern": "\"countryCode\":\"([A-Z]{2})\"",
                "flags": ""
              }
            ]
          }
        }
      },
      {
        "then": {
          "state": "no",
          "label": "未知"
        }
      }
    ]
  },
  {
    "id": "youtube",
    "name": "YouTube Premium",
    "cat": "global",
    "steps": [
      {
        "id": "a",
        "url": "https://www.youtube.com/premium",
        "maxBody": 524288,
        "headers": {
          "cookie": "YSC=BiCUU3-5Gdk; CONSENT=YES+cb.20220301-11-p0.en+FX+700; GPS=1; VISITOR_INFO1_LIVE=4VwPMkB7W5A; PREF=tz=Asia.Shanghai"
        }
      }
    ],
    "rules": [
      {
        "when": {
          "has": [
            {
              "$": "a.body"
            },
            "www.google.cn"
          ]
        },
        "then": {
          "state": "no",
          "region": "CN"
        }
      },
      {
        "when": {
          "any": [
            {
              "has": [
                {
                  "$": "a.body"
                },
                "purchaseButtonOverride"
              ]
            },
            {
              "has": [
                {
                  "$": "a.body"
                },
                "Start trial"
              ]
            },
            {
              "present": {
                "re": [
                  {
                    "$": "a.body"
                  },
                  {
                    "pattern": "\"(?:countryCode|contentRegion|GL)\":\"(\\w+)\"",
                    "flags": ""
                  }
                ]
              }
            }
          ]
        },
        "then": {
          "state": "yes",
          "region": {
            "re": [
              {
                "$": "a.body"
              },
              {
                "pattern": "\"(?:countryCode|contentRegion|GL)\":\"(\\w+)\"",
                "flags": ""
              }
            ]
          }
        }
      },
      {
        "then": {
          "state": "no"
        }
      }
    ]
  },
  {
    "id": "primevideo",
    "name": "Prime Video",
    "cat": "global",
    "steps": [
      {
        "id": "a",
        "url": "https://www.primevideo.com",
        "maxBody": 524288
      }
    ],
    "rules": [
      {
        "when": {
          "present": {
            "re": [
              {
                "$": "a.body"
              },
              {
                "pattern": "\"currentTerritory\":\"([A-Z]{2})\"",
                "flags": ""
              }
            ]
          }
        },
        "then": {
          "state": "yes",
          "region": {
            "re": [
              {
                "$": "a.body"
              },
              {
                "pattern": "\"currentTerritory\":\"([A-Z]{2})\"",
                "flags": ""
              }
            ]
          }
        }
      },
      {
        "then": {
          "state": "no",
          "label": "不支持"
        }
      }
    ]
  },
  {
    "id": "dazn",
    "name": "DAZN",
    "cat": "global",
    "steps": [
      {
        "id": "a",
        "url": "https://startup.core.indazn.com/misl/v5/Startup",
        "method": "POST",
        "headers": {
          "content-type": "application/json"
        },
        "body": "{\"LandingPageKey\":\"generic\",\"Languages\":\"zh-CN,zh,en\",\"Platform\":\"web\",\"PlatformAttributes\":{},\"Manufacturer\":\"\",\"PromoCode\":\"\",\"Version\":\"2\"}"
      }
    ],
    "rules": [
      {
        "when": {
          "any": [
            {
              "has": [
                {
                  "$": "a.body"
                },
                "Security policy has been breached"
              ]
            },
            {
              "has": [
                {
                  "$": "a.body"
                },
                "Forbidden"
              ]
            }
          ]
        },
        "then": {
          "state": "no",
          "label": "已封禁"
        }
      },
      {
        "when": {
          "eq": [
            {
              "json": [
                "a",
                "Region.isAllowed"
              ]
            },
            true
          ]
        },
        "then": {
          "state": "yes",
          "region": {
            "upper": {
              "json": [
                "a",
                "Region.GeolocatedCountry"
              ]
            }
          }
        }
      },
      {
        "when": {
          "eq": [
            {
              "json": [
                "a",
                "Region.isAllowed"
              ]
            },
            false
          ]
        },
        "then": {
          "state": "no"
        }
      },
      {
        "then": {
          "state": "info",
          "label": "不支持"
        }
      }
    ]
  },
  {
    "id": "tiktok",
    "name": "TikTok",
    "cat": "global",
    "steps": [
      {
        "id": "a",
        "url": "https://www.tiktok.com/",
        "maxBody": 65536
      },
      {
        "id": "b",
        "url": "https://www.tiktok.com/passport/web/store_region/",
        "method": "POST",
        "maxBody": 8192
      }
    ],
    "rules": [
      {
        "when": {
          "failed": "a"
        },
        "then": {
          "state": "fail"
        }
      },
      {
        "when": {
          "all": [
            {
              "match": [
                {
                  "$": "a.url"
                },
                {
                  "pattern": "\\/about|\\/status|landing",
                  "flags": ""
                }
              ]
            },
            {
              "eq": [
                {
                  "lower": {
                    "json": [
                      "b",
                      "data.store_region"
                    ]
                  }
                },
                "cn"
              ]
            }
          ]
        },
        "then": {
          "state": "partial",
          "label": "抖音提供"
        }
      },
      {
        "when": {
          "match": [
            {
              "$": "a.url"
            },
            {
              "pattern": "\\/about|\\/status|landing",
              "flags": ""
            }
          ]
        },
        "then": {
          "state": "no",
          "region": {
            "upper": {
              "json": [
                "b",
                "data.store_region"
              ]
            }
          }
        }
      },
      {
        "then": {
          "state": "yes",
          "region": {
            "upper": {
              "json": [
                "b",
                "data.store_region"
              ]
            }
          }
        }
      }
    ]
  },
  {
    "id": "reddit",
    "name": "Reddit",
    "cat": "global",
    "steps": [
      {
        "id": "a",
        "url": "https://www.reddit.com/",
        "maxBody": 1048576
      }
    ],
    "rules": [
      {
        "when": {
          "eq": [
            {
              "$": "a.status"
            },
            403
          ]
        },
        "then": {
          "state": "no"
        }
      },
      {
        "when": {
          "all": [
            {
              "eq": [
                {
                  "$": "a.status"
                },
                200
              ]
            },
            {
              "match": [
                {
                  "$": "a.body"
                },
                {
                  "pattern": "country=\"([A-Z]{2})\"",
                  "flags": ""
                }
              ]
            }
          ]
        },
        "then": {
          "state": "yes",
          "label": "人类解锁",
          "region": {
            "trim": {
              "concat": [
                {
                  "re": [
                    {
                      "$": "a.body"
                    },
                    {
                      "pattern": "country=\"([A-Z]{2})\"",
                      "flags": ""
                    }
                  ]
                },
                " ",
                {
                  "re": [
                    {
                      "$": "a.headers.server-timing"
                    },
                    {
                      "pattern": "p=([A-Z]+)",
                      "flags": ""
                    }
                  ]
                }
              ]
            }
          }
        }
      },
      {
        "when": {
          "eq": [
            {
              "$": "a.status"
            },
            200
          ]
        },
        "then": {
          "state": "partial",
          "label": "人机验证",
          "region": {
            "re": [
              {
                "$": "a.headers.server-timing"
              },
              {
                "pattern": "p=([A-Z]+)",
                "flags": ""
              }
            ]
          }
        }
      },
      {
        "then": {
          "state": "fail",
          "label": "未知响应"
        }
      }
    ]
  },
  {
    "id": "v2ex",
    "name": "V2EX",
    "cat": "global",
    "steps": [
      {
        "id": "a",
        "url": "https://www.v2ex.com/",
        "maxBody": 8192
      },
      {
        "id": "b",
        "url": "https://www.v2ex.com/cdn-cgi/trace",
        "maxBody": 4096
      }
    ],
    "rules": [
      {
        "when": {
          "eq": [
            {
              "$": "a.status"
            },
            200
          ]
        },
        "then": {
          "state": "yes",
          "region": {
            "re": [
              {
                "$": "b.body"
              },
              {
                "pattern": "\\bloc=([A-Z]{2})",
                "flags": ""
              }
            ]
          },
          "ip": {
            "re": [
              {
                "$": "b.body"
              },
              {
                "pattern": "\\bip=([^\\s]+)",
                "flags": ""
              }
            ]
          }
        }
      },
      {
        "then": {
          "state": "no",
          "label": "已封禁"
        }
      }
    ]
  },
  {
    "id": "steam",
    "name": "Steam 货币",
    "cat": "global",
    "steps": [
      {
        "id": "a",
        "url": "https://store.steampowered.com/app/761830",
        "maxBody": 262144
      }
    ],
    "rules": [
      {
        "when": {
          "present": {
            "re": [
              {
                "$": "a.body"
              },
              {
                "pattern": "itemprop=\"priceCurrency\"\\s+content=\"([A-Z]+)\"",
                "flags": ""
              }
            ]
          }
        },
        "then": {
          "state": "info",
          "label": {
            "re": [
              {
                "$": "a.body"
              },
              {
                "pattern": "itemprop=\"priceCurrency\"\\s+content=\"([A-Z]+)\"",
                "flags": ""
              }
            ]
          }
        }
      },
      {
        "then": {
          "state": "fail",
          "label": "无法获取"
        }
      }
    ]
  },
  {
    "id": "spotify",
    "name": "Spotify 区域",
    "cat": "global",
    "steps": [
      {
        "id": "a",
        "url": "https://www.spotify.com/tw/signup",
        "maxBody": 262144
      }
    ],
    "rules": [
      {
        "when": {
          "present": {
            "re": [
              {
                "$": "a.body"
              },
              {
                "pattern": "geoCountry\"?\\s*:\\s*\"([A-Z]{2})\"",
                "flags": ""
              }
            ]
          }
        },
        "then": {
          "state": "info",
          "label": {
            "re": [
              {
                "$": "a.body"
              },
              {
                "pattern": "geoCountry\"?\\s*:\\s*\"([A-Z]{2})\"",
                "flags": ""
              }
            ]
          }
        }
      },
      {
        "then": {
          "state": "fail",
          "label": "无法获取区域"
        }
      }
    ]
  },
  {
    "id": "pixiv",
    "name": "Pixiv",
    "cat": "global",
    "steps": [
      {
        "id": "a",
        "url": "https://www.pixiv.net/cdn-cgi/trace",
        "maxBody": 16384
      },
      {
        "id": "b",
        "url": "https://i.pximg.net/",
        "maxBody": 16384
      }
    ],
    "rules": [
      {
        "when": {
          "all": [
            {
              "ok": "a"
            },
            {
              "eq": [
                {
                  "$": "b.status"
                },
                200
              ]
            }
          ]
        },
        "then": {
          "state": "info",
          "label": "",
          "region": {
            "re": [
              {
                "$": "a.body"
              },
              {
                "pattern": "\\bloc=([A-Z0-9-]+)",
                "flags": "i"
              }
            ]
          },
          "ip": {
            "re": [
              {
                "$": "a.body"
              },
              {
                "pattern": "\\bip=([0-9a-f:.]+)",
                "flags": "i"
              }
            ]
          },
          "ms": {
            "$": "b.ms"
          }
        }
      },
      {
        "then": {
          "state": "fail",
          "label": "无法访问"
        }
      }
    ]
  },
  {
    "id": "google-search",
    "name": "谷歌搜索",
    "cat": "global",
    "steps": [
      {
        "id": "a",
        "url": "https://www.google.com",
        "maxBody": 2097152
      }
    ],
    "rules": [
      {
        "when": {
          "present": {
            "re": [
              {
                "$": "a.body"
              },
              {
                "pattern": "store\\.google\\.com\\/([A-Z]{2})\\b",
                "flags": ""
              }
            ]
          }
        },
        "then": {
          "state": "info",
          "label": {
            "upper": {
              "re": [
                {
                  "$": "a.body"
                },
                {
                  "pattern": "store\\.google\\.com\\/([A-Z]{2})\\b",
                  "flags": ""
                }
              ]
            }
          }
        }
      },
      {
        "when": {
          "present": {
            "re": [
              {
                "$": "a.body"
              },
              {
                "pattern": "utm_source=google-([A-Z]{2})\\b",
                "flags": ""
              }
            ]
          }
        },
        "then": {
          "state": "info",
          "label": {
            "upper": {
              "re": [
                {
                  "$": "a.body"
                },
                {
                  "pattern": "utm_source=google-([A-Z]{2})\\b",
                  "flags": ""
                }
              ]
            }
          }
        }
      },
      {
        "when": {
          "present": {
            "re": [
              {
                "$": "a.body"
              },
              {
                "pattern": "google\\.kHL='(?:[a-zA-Z]{2}-)?([a-zA-Z]{2})'",
                "flags": ""
              }
            ]
          }
        },
        "then": {
          "state": "info",
          "label": {
            "upper": {
              "re": [
                {
                  "$": "a.body"
                },
                {
                  "pattern": "google\\.kHL='(?:[a-zA-Z]{2}-)?([a-zA-Z]{2})'",
                  "flags": ""
                }
              ]
            }
          }
        }
      },
      {
        "when": {
          "present": {
            "re": [
              {
                "$": "a.body"
              },
              {
                "pattern": "[?&]gl=([a-zA-Z]{2})\\b",
                "flags": ""
              }
            ]
          }
        },
        "then": {
          "state": "info",
          "label": {
            "upper": {
              "re": [
                {
                  "$": "a.body"
                },
                {
                  "pattern": "[?&]gl=([a-zA-Z]{2})\\b",
                  "flags": ""
                }
              ]
            }
          }
        }
      },
      {
        "then": {
          "state": "fail",
          "label": "无法获取区域"
        }
      }
    ]
  },
  {
    "id": "google-play",
    "name": "谷歌商店",
    "cat": "global",
    "steps": [
      {
        "id": "a",
        "url": "https://play.google.com/store/games",
        "maxBody": 5242880
      }
    ],
    "rules": [
      {
        "when": {
          "present": {
            "re": [
              {
                "$": "a.body"
              },
              {
                "pattern": "\"fjF0tb\":\"([A-Z]{2})\"",
                "flags": "i"
              }
            ]
          }
        },
        "then": {
          "state": "info",
          "label": {
            "upper": {
              "re": [
                {
                  "$": "a.body"
                },
                {
                  "pattern": "\"fjF0tb\":\"([A-Z]{2})\"",
                  "flags": "i"
                }
              ]
            }
          }
        }
      },
      {
        "when": {
          "present": {
            "re": [
              {
                "$": "a.body"
              },
              {
                "pattern": "[?&]gl=([a-zA-Z]{2})\\b",
                "flags": ""
              }
            ]
          }
        },
        "then": {
          "state": "info",
          "label": {
            "upper": {
              "re": [
                {
                  "$": "a.body"
                },
                {
                  "pattern": "[?&]gl=([a-zA-Z]{2})\\b",
                  "flags": ""
                }
              ]
            }
          }
        }
      },
      {
        "when": {
          "present": {
            "re": [
              {
                "$": "a.body"
              },
              {
                "pattern": "\\[0\\s*,\\s*\"([A-Z]{3})\"\\s*,\\s*\"\"\\]",
                "flags": ""
              }
            ]
          }
        },
        "then": {
          "state": "info",
          "label": {
            "map": [
              {
                "upper": {
                  "re": [
                    {
                      "$": "a.body"
                    },
                    {
                      "pattern": "\\[0\\s*,\\s*\"([A-Z]{3})\"\\s*,\\s*\"\"\\]",
                      "flags": ""
                    }
                  ]
                }
              },
              {
                "USD": "US",
                "EUR": "EU",
                "GBP": "GB",
                "JPY": "JP",
                "KRW": "KR",
                "SGD": "SG",
                "HKD": "HK",
                "TWD": "TW",
                "CNY": "CN",
                "INR": "IN",
                "BRL": "BR",
                "MXN": "MX",
                "RUB": "RU",
                "CAD": "CA",
                "AUD": "AU",
                "CHF": "CH",
                "SEK": "SE",
                "NOK": "NO",
                "DKK": "DK",
                "NZD": "NZ"
              },
              {
                "upper": {
                  "re": [
                    {
                      "$": "a.body"
                    },
                    {
                      "pattern": "\\[0\\s*,\\s*\"([A-Z]{3})\"\\s*,\\s*\"\"\\]",
                      "flags": ""
                    }
                  ]
                }
              }
            ]
          }
        }
      },
      {
        "then": {
          "state": "fail",
          "label": "无法获取区域"
        }
      }
    ]
  },
  {
    "id": "wikipedia-edit",
    "name": "维基百科编辑",
    "cat": "global",
    "steps": [
      {
        "id": "a",
        "url": "https://zh.wikipedia.org/w/api.php?action=query&list=blocks&bklimit=100&format=json",
        "maxBody": 131072
      }
    ],
    "rules": [
      {
        "when": {
          "present": {
            "wikiDuration": "a"
          }
        },
        "then": {
          "state": "no",
          "label": {
            "concat": [
              "封禁时间 ",
              {
                "wikiDuration": "a"
              }
            ]
          },
          "tooltip": {
            "json": [
              "a",
              "query.blocks.0.reason"
            ]
          }
        }
      },
      {
        "then": {
          "state": "yes",
          "label": "可编辑"
        }
      }
    ]
  },
  {
    "id": "hulu-us",
    "name": "Hulu",
    "cat": "na",
    "steps": [
      {
        "id": "a",
        "url": "https://www.hulu.com/",
        "maxBody": 8192
      }
    ],
    "rules": [
      {
        "when": {
          "has": [
            {
              "$": "a.url"
            },
            "disneyplus.com"
          ]
        },
        "then": {
          "state": "no",
          "label": "不解锁重定向到Disney+"
        }
      },
      {
        "when": {
          "has": [
            {
              "$": "a.url"
            },
            "hulu.com"
          ]
        },
        "then": {
          "state": "yes"
        }
      },
      {
        "then": {
          "state": "fail",
          "label": {
            "concat": [
              "HTTP ",
              {
                "$": "a.status"
              }
            ]
          }
        }
      }
    ]
  },
  {
    "id": "fox",
    "name": "FOX",
    "cat": "na",
    "steps": [
      {
        "id": "a",
        "url": "https://x-live-fox-stgec.uplynk.com/ausw/slices/8d1/d8e6eec26bf544f084bad49a7fa2eac5/8d1de292bcc943a6b886d029e6c0dc87/G00000000.ts?pbs=c61e60ee63ce43359679fb9f65d21564&cloud=aws&si=0",
        "maxBody": 1024
      }
    ],
    "rules": [
      {
        "when": {
          "eq": [
            {
              "$": "a.status"
            },
            200
          ]
        },
        "then": {
          "state": "yes"
        }
      },
      {
        "when": {
          "eq": [
            {
              "$": "a.status"
            },
            403
          ]
        },
        "then": {
          "state": "no"
        }
      },
      {
        "then": {
          "state": "fail",
          "label": {
            "concat": [
              "HTTP ",
              {
                "$": "a.status"
              }
            ]
          }
        }
      }
    ]
  },
  {
    "id": "plutotv",
    "name": "Pluto TV",
    "cat": "na",
    "steps": [
      {
        "id": "a",
        "url": "https://pluto.tv/",
        "maxBody": 8192
      }
    ],
    "rules": [
      {
        "when": {
          "has": [
            {
              "$": "a.url"
            },
            "thanks-for-watching"
          ]
        },
        "then": {
          "state": "no"
        }
      },
      {
        "then": {
          "state": "yes"
        }
      }
    ]
  },
  {
    "id": "peacock",
    "name": "Peacock TV",
    "cat": "na",
    "steps": [
      {
        "id": "a",
        "url": "https://www.peacocktv.com/",
        "maxBody": 8192
      }
    ],
    "rules": [
      {
        "when": {
          "has": [
            {
              "$": "a.url"
            },
            "unavailable"
          ]
        },
        "then": {
          "state": "no"
        }
      },
      {
        "then": {
          "state": "yes"
        }
      }
    ]
  },
  {
    "id": "paramount",
    "name": "Paramount+",
    "cat": "na",
    "steps": [
      {
        "id": "a",
        "url": "https://www.paramountplus.com/",
        "maxBody": 8192
      }
    ],
    "rules": [
      {
        "when": {
          "any": [
            {
              "has": [
                {
                  "$": "a.url"
                },
                "intl"
              ]
            },
            {
              "eq": [
                {
                  "$": "a.status"
                },
                403
              ]
            }
          ]
        },
        "then": {
          "state": "no"
        }
      },
      {
        "when": {
          "eq": [
            {
              "$": "a.status"
            },
            200
          ]
        },
        "then": {
          "state": "yes",
          "region": {
            "upper": {
              "first": [
                {
                  "re": [
                    {
                      "$": "a.url"
                    },
                    {
                      "pattern": "paramountplus\\.com\\/([a-z]{2})\\/",
                      "flags": ""
                    }
                  ]
                },
                "us"
              ]
            }
          }
        }
      },
      {
        "then": {
          "state": "fail",
          "label": {
            "concat": [
              "HTTP ",
              {
                "$": "a.status"
              }
            ]
          }
        }
      }
    ]
  },
  {
    "id": "slingtv",
    "name": "Sling TV",
    "cat": "na",
    "steps": [
      {
        "id": "a",
        "url": "https://p-geo.movetv.com/geo",
        "maxBody": 8192
      }
    ],
    "rules": [
      {
        "when": {
          "eq": [
            {
              "json": [
                "a",
                "ip_restricted"
              ]
            },
            true
          ]
        },
        "then": {
          "state": "no",
          "region": {
            "upper": {
              "json": [
                "a",
                "country"
              ]
            }
          }
        }
      },
      {
        "then": {
          "state": "yes",
          "region": {
            "upper": {
              "json": [
                "a",
                "country"
              ]
            }
          }
        }
      }
    ]
  },
  {
    "id": "fubotv",
    "name": "Fubo TV",
    "cat": "na",
    "steps": [
      {
        "id": "a",
        "url": "https://api.fubo.tv/appconfig/v1/homepage?platform=web&client_version=R20230310.1&nav=v0",
        "maxBody": 32768
      }
    ],
    "rules": [
      {
        "when": {
          "has": [
            {
              "$": "a.body"
            },
            "Forbidden IP"
          ]
        },
        "then": {
          "state": "no"
        }
      },
      {
        "then": {
          "state": "yes"
        }
      }
    ]
  },
  {
    "id": "bbc",
    "name": "BBC iPlayer",
    "cat": "eu",
    "steps": [
      {
        "id": "a",
        "url": "https://open.live.bbc.co.uk/mediaselector/6/select/version/2.0/mediaset/pc/vpid/bbc_one_london/format/json/jsfunc/JS_callbacks0",
        "maxBody": 32768
      }
    ],
    "rules": [
      {
        "when": {
          "has": [
            {
              "$": "a.body"
            },
            "geolocation"
          ]
        },
        "then": {
          "state": "no"
        }
      },
      {
        "then": {
          "state": "yes"
        }
      }
    ]
  },
  {
    "id": "channel4",
    "name": "Channel 4",
    "cat": "eu",
    "steps": [
      {
        "id": "a",
        "url": "https://www.channel4.com/simulcast/channels/C4",
        "maxBody": 4096
      }
    ],
    "rules": [
      {
        "when": {
          "eq": [
            {
              "$": "a.status"
            },
            403
          ]
        },
        "then": {
          "state": "no"
        }
      },
      {
        "when": {
          "eq": [
            {
              "$": "a.status"
            },
            200
          ]
        },
        "then": {
          "state": "yes"
        }
      },
      {
        "then": {
          "state": "fail",
          "label": {
            "concat": [
              "HTTP ",
              {
                "$": "a.status"
              }
            ]
          }
        }
      }
    ]
  },
  {
    "id": "itv",
    "name": "ITV Hub",
    "cat": "eu",
    "steps": [
      {
        "id": "a",
        "url": "https://simulcast.itv.com/playlist/itvonline/ITV",
        "maxBody": 4096
      }
    ],
    "rules": [
      {
        "when": {
          "eq": [
            {
              "$": "a.status"
            },
            404
          ]
        },
        "then": {
          "state": "yes"
        }
      },
      {
        "when": {
          "eq": [
            {
              "$": "a.status"
            },
            403
          ]
        },
        "then": {
          "state": "no"
        }
      },
      {
        "then": {
          "state": "fail",
          "label": {
            "concat": [
              "HTTP ",
              {
                "$": "a.status"
              }
            ]
          }
        }
      }
    ]
  },
  {
    "id": "britbox",
    "name": "BritBox",
    "cat": "eu",
    "steps": [
      {
        "id": "a",
        "url": "https://www.britbox.com/",
        "maxBody": 8192
      }
    ],
    "rules": [
      {
        "when": {
          "has": [
            {
              "$": "a.url"
            },
            "locationnotvalidated"
          ]
        },
        "then": {
          "state": "no"
        }
      },
      {
        "then": {
          "state": "yes"
        }
      }
    ]
  },
  {
    "id": "joyn",
    "name": "Joyn",
    "cat": "eu",
    "steps": [
      {
        "id": "a",
        "url": "https://auth.joyn.de/auth/anonymous",
        "method": "POST",
        "headers": {
          "content-type": "application/json"
        },
        "body": "{\"client_id\":\"b74b9f27-a994-4c45-b7eb-5b81b1c856e7\",\"client_name\":\"web\",\"anon_device_id\":\"b74b9f27-a994-4c45-b7eb-5b81b1c856e7\"}"
      },
      {
        "id": "b",
        "url": "https://api.joyn.de/content/entitlement-token",
        "method": "POST",
        "needs": [
          "a"
        ],
        "vars": {
          "tok": {
            "json": [
              "a",
              "access_token"
            ]
          }
        },
        "headers": {
          "x-api-key": "36lp1t4wto5uu2i2nk57ywy9on1ns5yg",
          "authorization": "Bearer ${tok}",
          "content-type": "application/json"
        },
        "body": "{\"content_id\":\"daserste-de-hd\",\"content_type\":\"LIVE\"}"
      }
    ],
    "rules": [
      {
        "when": {
          "failed": "b"
        },
        "then": {
          "state": "fail",
          "label": "链路中断"
        }
      },
      {
        "when": {
          "eq": [
            {
              "json": [
                "b",
                "code"
              ]
            },
            "ENT_AssetNotAvailableInCountry"
          ]
        },
        "then": {
          "state": "no"
        }
      },
      {
        "then": {
          "state": "yes"
        }
      }
    ]
  },
  {
    "id": "zdf",
    "name": "ZDF",
    "cat": "eu",
    "steps": [
      {
        "id": "a",
        "url": "https://ssl.zdf.de/geo/de/geo.txt/",
        "ua": "dalvik",
        "maxBody": 4096
      }
    ],
    "rules": [
      {
        "when": {
          "eq": [
            {
              "$": "a.status"
            },
            404
          ]
        },
        "then": {
          "state": "yes"
        }
      },
      {
        "when": {
          "eq": [
            {
              "$": "a.status"
            },
            403
          ]
        },
        "then": {
          "state": "no"
        }
      },
      {
        "then": {
          "state": "fail",
          "label": {
            "concat": [
              "HTTP ",
              {
                "$": "a.status"
              }
            ]
          }
        }
      }
    ]
  },
  {
    "id": "raiplay",
    "name": "Rai Play",
    "cat": "eu",
    "steps": [
      {
        "id": "a",
        "url": "https://mediapolisvod.rai.it/relinker/relinkerServlet.htm?cont=LK3VlpPpPlussa2pPpPlussssSlashD7pOsX3K5HRQeeqqEEqualeeqqEEqual&output=62",
        "maxBody": 16384
      }
    ],
    "rules": [
      {
        "when": {
          "has": [
            {
              "$": "a.body"
            },
            "no_available"
          ]
        },
        "then": {
          "state": "no"
        }
      },
      {
        "then": {
          "state": "yes"
        }
      }
    ]
  },
  {
    "id": "canalplus",
    "name": "Canal+",
    "cat": "eu",
    "steps": [
      {
        "id": "a",
        "url": "https://boutique-tunnel.canalplus.com/",
        "maxBody": 8192
      }
    ],
    "rules": [
      {
        "when": {
          "has": [
            {
              "$": "a.url"
            },
            "other-country-blocking"
          ]
        },
        "then": {
          "state": "no"
        }
      },
      {
        "then": {
          "state": "yes"
        }
      }
    ]
  },
  {
    "id": "francetv",
    "name": "France.tv",
    "cat": "eu",
    "steps": [
      {
        "id": "a",
        "url": "https://geo-info.ftven.fr/ws/edgescape.json",
        "maxBody": 8192
      }
    ],
    "rules": [
      {
        "when": {
          "eq": [
            {
              "json": [
                "a",
                "reponse.geo_info.country_code"
              ]
            },
            "FR"
          ]
        },
        "then": {
          "state": "yes",
          "region": "FR"
        }
      },
      {
        "then": {
          "state": "no",
          "region": {
            "json": [
              "a",
              "reponse.geo_info.country_code"
            ]
          }
        }
      }
    ]
  },
  {
    "id": "npo",
    "name": "NPO Start Plus",
    "cat": "eu",
    "steps": [
      {
        "id": "a",
        "url": "https://npo.nl/start/api/domain/player-token?productId=BV_101410466",
        "maxBody": 8192
      },
      {
        "id": "b",
        "url": "https://prod.npoplayer.nl/stream-link",
        "method": "POST",
        "needs": [
          "a"
        ],
        "vars": {
          "tok": {
            "json": [
              "a",
              "token"
            ]
          }
        },
        "headers": {
          "authorization": "${tok}"
        },
        "maxBody": 8192
      }
    ],
    "rules": [
      {
        "when": {
          "failed": "b"
        },
        "then": {
          "state": "fail",
          "label": "链路中断"
        }
      },
      {
        "when": {
          "eq": [
            {
              "json": [
                "b",
                "status"
              ]
            },
            451
          ]
        },
        "then": {
          "state": "no"
        }
      },
      {
        "then": {
          "state": "yes"
        }
      }
    ]
  },
  {
    "id": "viutv",
    "name": "Viu.TV",
    "cat": "hk",
    "steps": [
      {
        "id": "a",
        "url": "https://api.viu.now.com/p8/3/getLiveURL",
        "method": "POST",
        "headers": {
          "content-type": "application/json"
        },
        "body": "{\"callerReferenceNo\":\"20210726112323\",\"contentId\":\"099\",\"contentType\":\"Channel\",\"channelno\":\"099\",\"mode\":\"prod\",\"deviceId\":\"29b3cb117a635d5b56\",\"deviceType\":\"ANDROID_WEB\"}",
        "maxBody": 8192
      }
    ],
    "rules": [
      {
        "when": {
          "eq": [
            {
              "json": [
                "a",
                "responseCode"
              ]
            },
            "SUCCESS"
          ]
        },
        "then": {
          "state": "yes"
        }
      },
      {
        "when": {
          "eq": [
            {
              "json": [
                "a",
                "responseCode"
              ]
            },
            "GEO_CHECK_FAIL"
          ]
        },
        "then": {
          "state": "no"
        }
      },
      {
        "then": {
          "state": "fail",
          "label": {
            "first": [
              {
                "json": [
                  "a",
                  "responseCode"
                ]
              },
              "未知响应"
            ]
          }
        }
      }
    ]
  },
  {
    "id": "mytvsuper",
    "name": "MyTVSuper",
    "cat": "hk",
    "steps": [
      {
        "id": "a",
        "url": "https://www.mytvsuper.com/api/auth/getSession/self/",
        "maxBody": 8192
      }
    ],
    "rules": [
      {
        "when": {
          "eq": [
            {
              "json": [
                "a",
                "region"
              ]
            },
            1
          ]
        },
        "then": {
          "state": "yes"
        }
      },
      {
        "then": {
          "state": "no"
        }
      }
    ]
  },
  {
    "id": "kktv",
    "name": "KKTV",
    "cat": "tw",
    "steps": [
      {
        "id": "a",
        "url": "https://api.kktv.me/v3/ipcheck",
        "maxBody": 4096
      }
    ],
    "rules": [
      {
        "when": {
          "eq": [
            {
              "json": [
                "a",
                "data.is_allowed"
              ]
            },
            true
          ]
        },
        "then": {
          "state": "yes",
          "region": {
            "json": [
              "a",
              "data.country"
            ]
          }
        }
      },
      {
        "then": {
          "state": "no",
          "region": {
            "json": [
              "a",
              "data.country"
            ]
          }
        }
      }
    ]
  },
  {
    "id": "litv",
    "name": "LiTV",
    "cat": "tw",
    "steps": [
      {
        "id": "a",
        "url": "https://pusti.svc.litv.tv/puid",
        "method": "POST",
        "headers": {
          "content-type": "application/json"
        },
        "body": "{\"jsonrpc\":\"2.0\",\"id\":100,\"method\":\"PustiService.PUID\",\"params\":{\"version\":\"2.0\",\"device_id\":\"\",\"device_category\":\"LTWEB00\",\"puid\":\"\",\"aaid\":\"\",\"idfa\":\"\"}}",
        "maxBody": 4096
      },
      {
        "id": "b",
        "url": "https://proxy.svc.litv.tv/cdi/v2/rpc",
        "method": "POST",
        "needs": [
          "a"
        ],
        "vars": {
          "puid": {
            "json": [
              "a",
              "result.puid"
            ]
          }
        },
        "headers": {
          "content-type": "application/json"
        },
        "body": "{\"jsonrpc\":\"2.0\",\"method\":\"LoadService.GetURLsNoAuth\",\"params\":{\"assetId\":\"vod70810-000001M001_1500K\",\"mediaType\":\"vod\",\"puid\":\"${puid}\",\"deviceType\":\"pc\"},\"id\":1}",
        "maxBody": 8192
      }
    ],
    "rules": [
      {
        "when": {
          "failed": "a"
        },
        "then": {
          "state": "fail",
          "label": "PUID 获取失败"
        }
      },
      {
        "when": {
          "present": {
            "json": [
              "b",
              "result.AssetURLs"
            ]
          }
        },
        "then": {
          "state": "yes"
        }
      },
      {
        "when": {
          "eq": [
            {
              "json": [
                "b",
                "error.code"
              ]
            },
            42000087
          ]
        },
        "then": {
          "state": "no"
        }
      },
      {
        "when": {
          "eq": [
            {
              "json": [
                "b",
                "error.code"
              ]
            },
            42000075
          ]
        },
        "then": {
          "state": "partial",
          "label": "需订阅/地区异常"
        }
      },
      {
        "then": {
          "state": "fail",
          "label": "未知响应"
        }
      }
    ]
  },
  {
    "id": "4gtv",
    "name": "4GTV.TV",
    "cat": "tw",
    "steps": [
      {
        "id": "a",
        "url": "https://api2.4gtv.tv//Vod/GetVodUrl3",
        "method": "POST",
        "headers": {
          "content-type": "application/x-www-form-urlencoded"
        },
        "vars": {
          "enc": "tA2JfRx%2F7zwFdyuUuRbHlfu8KGQwFUHJwV6Qb6%2BPHUV30jdyMZuP94dIwgsAbTox6bWBvHY%2FkSAC3qOBw4xhsnN2AXczyALMjfxGqwDrB6mx1yHyun0YMxbYPf5HBe7Z"
        },
        "body": "value=${enc}",
        "maxBody": 8192
      }
    ],
    "rules": [
      {
        "when": {
          "eq": [
            {
              "json": [
                "a",
                "Success"
              ]
            },
            true
          ]
        },
        "then": {
          "state": "yes"
        }
      },
      {
        "when": {
          "eq": [
            {
              "json": [
                "a",
                "Success"
              ]
            },
            false
          ]
        },
        "then": {
          "state": "no"
        }
      },
      {
        "then": {
          "state": "fail",
          "label": "未知响应"
        }
      }
    ]
  },
  {
    "id": "hami",
    "name": "Hami Video",
    "cat": "tw",
    "steps": [
      {
        "id": "a",
        "url": "https://hamivideo.hinet.net/api/play.do?id=OTT_VOD_0000249064&freeProduct=1",
        "maxBody": 8192
      }
    ],
    "rules": [
      {
        "when": {
          "eq": [
            {
              "json": [
                "a",
                "code"
              ]
            },
            "06001-107"
          ]
        },
        "then": {
          "state": "yes"
        }
      },
      {
        "when": {
          "eq": [
            {
              "json": [
                "a",
                "code"
              ]
            },
            "06001-106"
          ]
        },
        "then": {
          "state": "no"
        }
      },
      {
        "then": {
          "state": "fail",
          "label": "未知响应"
        }
      }
    ]
  },
  {
    "id": "catchplay",
    "name": "CatchPlay+",
    "cat": "tw",
    "steps": [
      {
        "id": "a",
        "url": "https://sunapi.catchplay.com/geo",
        "headers": {
          "accept": "application/json",
          "authorization": "Basic NTQ3MzM0NDgtYTU3Yi00MjU2LWE4MTEtMzdlYzNkNjJmM2E0Ok90QzR3elJRR2hLQ01sSDc2VEoy"
        },
        "maxBody": 8192
      }
    ],
    "rules": [
      {
        "when": {
          "eq": [
            {
              "re": [
                {
                  "$": "a.body"
                },
                {
                  "pattern": "(?:\"code\"\\s*:\\s*\"?|<code[^>]*>)(\\d+)",
                  "flags": ""
                }
              ]
            },
            "0"
          ]
        },
        "then": {
          "state": "yes",
          "region": {
            "re": [
              {
                "$": "a.body"
              },
              {
                "pattern": "(?:\"isoCode\"\\s*:\\s*\"|<isoCode[^>]*>)([A-Z]{2})",
                "flags": ""
              }
            ]
          }
        }
      },
      {
        "when": {
          "eq": [
            {
              "re": [
                {
                  "$": "a.body"
                },
                {
                  "pattern": "(?:\"code\"\\s*:\\s*\"?|<code[^>]*>)(\\d+)",
                  "flags": ""
                }
              ]
            },
            "100016"
          ]
        },
        "then": {
          "state": "no"
        }
      },
      {
        "then": {
          "state": "fail",
          "label": "未知响应"
        }
      }
    ]
  },
  {
    "id": "bahamut",
    "name": "巴哈姆特動畫瘋",
    "cat": "tw",
    "steps": [
      {
        "id": "a",
        "url": "https://ani.gamer.com.tw/ajax/getdeviceid.php",
        "maxBody": 4096
      },
      {
        "id": "b",
        "url": "https://ani.gamer.com.tw/ajax/token.php?adID=89422&sn=38832&device=${dev}",
        "needs": [
          "a"
        ],
        "vars": {
          "dev": {
            "json": [
              "a",
              "deviceid"
            ]
          },
          "ck": {
            "cookies": "a"
          }
        },
        "headers": {
          "cookie": "${ck}"
        },
        "maxBody": 8192
      },
      {
        "id": "c",
        "url": "https://ani.gamer.com.tw/ajax/token.php?adID=89422&sn=37783&device=${dev}",
        "needs": [
          "a"
        ],
        "vars": {
          "dev": {
            "json": [
              "a",
              "deviceid"
            ]
          },
          "ck": {
            "cookies": "a"
          }
        },
        "headers": {
          "cookie": "${ck}"
        },
        "maxBody": 8192
      }
    ],
    "rules": [
      {
        "when": {
          "all": [
            {
              "has": [
                {
                  "$": "b.body"
                },
                "animeSn"
              ]
            },
            {
              "has": [
                {
                  "$": "c.body"
                },
                "animeSn"
              ]
            }
          ]
        },
        "then": {
          "state": "yes",
          "region": "TW"
        }
      },
      {
        "when": {
          "has": [
            {
              "$": "c.body"
            },
            "animeSn"
          ]
        },
        "then": {
          "state": "yes",
          "region": "HK/MO"
        }
      },
      {
        "then": {
          "state": "no"
        }
      }
    ]
  },
  {
    "id": "abema",
    "name": "Abema.TV",
    "cat": "jp",
    "steps": [
      {
        "id": "a",
        "url": "https://api.abema.io/v1/ip/check?device=android",
        "ua": "dalvik",
        "maxBody": 8192
      }
    ],
    "rules": [
      {
        "when": {
          "eq": [
            {
              "json": [
                "a",
                "isoCountryCode"
              ]
            },
            "JP"
          ]
        },
        "then": {
          "state": "yes",
          "region": "JP"
        }
      },
      {
        "when": {
          "present": {
            "json": [
              "a",
              "isoCountryCode"
            ]
          }
        },
        "then": {
          "state": "partial",
          "label": "仅海外版",
          "region": {
            "json": [
              "a",
              "isoCountryCode"
            ]
          }
        }
      },
      {
        "then": {
          "state": "no"
        }
      }
    ]
  },
  {
    "id": "niconico",
    "name": "Niconico",
    "cat": "jp",
    "steps": [
      {
        "id": "a",
        "url": "https://www.nicovideo.jp/watch/so23017073",
        "maxBody": 4096
      }
    ],
    "rules": [
      {
        "when": {
          "eq": [
            {
              "$": "a.status"
            },
            200
          ]
        },
        "then": {
          "state": "yes"
        }
      },
      {
        "when": {
          "eq": [
            {
              "$": "a.status"
            },
            400
          ]
        },
        "then": {
          "state": "no"
        }
      },
      {
        "then": {
          "state": "fail",
          "label": {
            "concat": [
              "HTTP ",
              {
                "$": "a.status"
              }
            ]
          }
        }
      }
    ]
  },
  {
    "id": "hulu-jp",
    "name": "Hulu Japan",
    "cat": "jp",
    "steps": [
      {
        "id": "a",
        "url": "https://id.hulu.jp/signup/email",
        "maxBody": 8192
      }
    ],
    "rules": [
      {
        "when": {
          "any": [
            {
              "has": [
                {
                  "$": "a.url"
                },
                "restrict"
              ]
            },
            {
              "eq": [
                {
                  "$": "a.status"
                },
                403
              ]
            }
          ]
        },
        "then": {
          "state": "no"
        }
      },
      {
        "then": {
          "state": "yes"
        }
      }
    ]
  },
  {
    "id": "tver",
    "name": "TVer",
    "cat": "jp",
    "steps": [
      {
        "id": "a",
        "url": "https://player.tver.jp/player/streaks_info_v2.json",
        "headers": {
          "Referer": "https://tver.jp/",
          "Origin": "https://tver.jp"
        },
        "maxBody": 65536
      },
      {
        "id": "b",
        "url": "https://playback.api.streaks.jp/v1/projects/tver-simul-ntv/medias/ref:simul-ntv",
        "needs": [
          "a"
        ],
        "vars": {
          "key": {
            "json": [
              "a",
              "tver-simul-ntv.api_key.key01"
            ]
          }
        },
        "headers": {
          "x-streaks-api-key": "${key}"
        },
        "maxBody": 16384
      }
    ],
    "rules": [
      {
        "when": {
          "failed": "a"
        },
        "then": {
          "state": "fail",
          "label": "key 获取失败"
        }
      },
      {
        "when": {
          "has": [
            {
              "$": "b.body"
            },
            "project_id"
          ]
        },
        "then": {
          "state": "yes"
        }
      },
      {
        "when": {
          "eq": [
            {
              "$": "b.status"
            },
            403
          ]
        },
        "then": {
          "state": "no"
        }
      },
      {
        "then": {
          "state": "fail",
          "label": "未知响应"
        }
      }
    ]
  },
  {
    "id": "dmm",
    "name": "DMM",
    "cat": "jp",
    "steps": [
      {
        "id": "a",
        "url": "https://api.beacon.dmm.com/v1/streaming/start",
        "method": "POST",
        "headers": {
          "content-type": "application/json"
        },
        "body": "{\"player_name\":\"dmmtv_browser\",\"player_version\":\"0.0.0\",\"content_type_detail\":\"VOD_SVOD\",\"content_id\":\"11uvjcm4fw2wdu7drtd1epnvz\",\"purchase_product_id\":null}",
        "maxBody": 8192
      }
    ],
    "rules": [
      {
        "when": {
          "eq": [
            {
              "json": [
                "a",
                "block_status"
              ]
            },
            "FOREIGN"
          ]
        },
        "then": {
          "state": "no"
        }
      },
      {
        "when": {
          "in": [
            {
              "json": [
                "a",
                "block_status"
              ]
            },
            [
              "UNAUTHORIZED",
              "LOGIN_REQUIRED",
              "PURCHASE_REQUIRED"
            ]
          ]
        },
        "then": {
          "state": "yes"
        }
      },
      {
        "when": {
          "eq": [
            {
              "json": [
                "a",
                "is_blocked"
              ]
            },
            false
          ]
        },
        "then": {
          "state": "yes"
        }
      },
      {
        "then": {
          "state": "fail",
          "label": {
            "first": [
              {
                "json": [
                  "a",
                  "block_status"
                ]
              },
              "未知响应"
            ]
          }
        }
      }
    ]
  },
  {
    "id": "radiko",
    "name": "Radiko",
    "cat": "jp",
    "steps": [
      {
        "id": "a",
        "url": "https://radiko.jp/area?_=${ts}",
        "maxBody": 4096
      }
    ],
    "rules": [
      {
        "when": {
          "has": [
            {
              "$": "a.body"
            },
            "class=\"OUT\""
          ]
        },
        "then": {
          "state": "no"
        }
      },
      {
        "when": {
          "has": [
            {
              "$": "a.body"
            },
            "JAPAN"
          ]
        },
        "then": {
          "state": "yes",
          "label": {
            "re": [
              {
                "$": "a.body"
              },
              {
                "pattern": "<span class=\"[^\"]*\">([^<]+)<",
                "flags": ""
              }
            ]
          }
        }
      },
      {
        "then": {
          "state": "info",
          "label": "不支持"
        }
      }
    ]
  },
  {
    "id": "fod",
    "name": "FOD (富士电视台)",
    "cat": "jp",
    "steps": [
      {
        "id": "a",
        "url": "https://geocontrol1.stream.ne.jp/fod-geo/check.xml?time=${ts}",
        "maxBody": 4096
      }
    ],
    "rules": [
      {
        "when": {
          "has": [
            {
              "$": "a.body"
            },
            "true"
          ]
        },
        "then": {
          "state": "yes"
        }
      },
      {
        "then": {
          "state": "no"
        }
      }
    ]
  },
  {
    "id": "kancolle",
    "name": "舰队 Collection",
    "cat": "jp",
    "steps": [
      {
        "id": "a",
        "url": "http://w00g.kancolle-server.com/kcscontents/news/",
        "ua": "dalvik",
        "maxBody": 4096
      }
    ],
    "rules": [
      {
        "when": {
          "eq": [
            {
              "$": "a.status"
            },
            200
          ]
        },
        "then": {
          "state": "yes"
        }
      },
      {
        "when": {
          "eq": [
            {
              "$": "a.status"
            },
            403
          ]
        },
        "then": {
          "state": "no"
        }
      },
      {
        "then": {
          "state": "fail",
          "label": {
            "concat": [
              "HTTP ",
              {
                "$": "a.status"
              }
            ]
          }
        }
      }
    ]
  },
  {
    "id": "pcr",
    "name": "公主连结 Re:Dive",
    "cat": "jp",
    "steps": [
      {
        "id": "a",
        "url": "https://api-priconne-redive.cygames.jp/",
        "ua": "dalvik",
        "maxBody": 4096
      }
    ],
    "rules": [
      {
        "when": {
          "eq": [
            {
              "$": "a.status"
            },
            404
          ]
        },
        "then": {
          "state": "yes"
        }
      },
      {
        "when": {
          "eq": [
            {
              "$": "a.status"
            },
            403
          ]
        },
        "then": {
          "state": "no"
        }
      },
      {
        "then": {
          "state": "fail",
          "label": {
            "concat": [
              "HTTP ",
              {
                "$": "a.status"
              }
            ]
          }
        }
      }
    ]
  }
];
